import asyncio
import logging
import sys
import time
from collections.abc import Iterable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from .output import Output
from .supervisor import Supervisor, SupervisorExited
from .test import Test, TestResult

logger = logging.getLogger(__name__)


class TestRunner:
    def __init__(
        self,
        tests: Iterable[Test],
        supervisors: Iterable[Supervisor],
        output: Output,
        keep_alive: bool = False,
        test_timeout: int | None = None,
        bpftrace: str | Path | None = None,
        probe_interval: int = 0,
        max_supervisor_restarts: int = 3,
        dmesg: bool = False,
    ):
        self.tests: list[Test] = list(tests)
        self.supervisors: list[Supervisor] = list(supervisors)
        self.output: Output = output
        self.keep_alive: bool = keep_alive
        self.test_timeout: int | None = test_timeout
        self.probe_interval: int = probe_interval
        self.max_supervisor_restarts: int = max_supervisor_restarts
        self.dmesg: bool = dmesg

        self._death_counts: dict[str, int] = {}

        self.bpftrace_command: str | None
        match bpftrace:
            case Path():
                self.bpftrace_command = f"bpftrace {str(bpftrace)}"
            case str():
                self.bpftrace_command = f"bpftrace -e {bpftrace}"
            case _:
                self.bpftrace_command = None

    async def run(self):
        try:
            with self.output.running_tests(len(self.tests)):
                async with asyncio.TaskGroup() as tg:
                    for supervisor in self.supervisors:
                        _ = tg.create_task(self._worker(supervisor))

            if self.keep_alive:
                with self.output.keeping_alive():
                    while True:
                        await asyncio.sleep(1)
        finally:
            # Safety net: clean up any supervisors still running (keep_alive path)
            for supervisor in self.supervisors:
                if not supervisor.exited:
                    await supervisor.__aexit__(*sys.exc_info())
            self.output.print_summary()

    # --- Worker ---

    async def _worker(self, supervisor: Supervisor):
        try:
            with self.output.spawning_supervisor(supervisor):
                _ = await supervisor.__aenter__()
        except (TimeoutError, RuntimeError, OSError):
            logger.exception("supervisor %r failed to spawn", supervisor)
            return

        try:
            if self.probe_interval <= 0:
                try:
                    await self._run_tests_loop(supervisor)
                except OSError:
                    logger.exception("worker for %r crashed", supervisor)
                    self.output.supervisor_died(supervisor)
                return

            # Probing + restart loop
            while True:
                current_test: list[Test | None] = [None]
                died = False
                try:
                    async with asyncio.TaskGroup() as tg:
                        probe_task = tg.create_task(self._probe_loop(supervisor))
                        _ = tg.create_task(
                            self._run_tests_then_cancel(
                                supervisor, probe_task, current_test
                            )
                        )
                except* (SupervisorExited, OSError):
                    died = True
                    test = current_test[0]
                    self.output.supervisor_died(
                        supervisor, test.name if test is not None else None
                    )

                    if test is not None:
                        self.output.record_retry(
                            test,
                            TestResult.from_error(
                                test.name,
                                "supervisor died",
                                0.0,
                                datetime.now(),
                            ),
                        )

                        count = self._death_counts.get(test.name, 0) + 1
                        self._death_counts[test.name] = count
                        if count >= self.max_supervisor_restarts:
                            self.output.finished_test(
                                test,
                                TestResult.from_error(
                                    test.name,
                                    f"killed supervisor {count} times",
                                    0.0,
                                    datetime.now(),
                                ),
                            )
                        else:
                            test.retry()
                            self.tests.append(test)

                if not died:
                    return  # Normal completion

                if not self.tests:
                    return

                try:
                    with self.output.respawning_supervisor(supervisor):
                        await supervisor.__aexit__(None, None, None)
                        _ = await supervisor.__aenter__()
                except Exception:
                    logger.exception("failed to restart %s", supervisor)
                    return
        finally:
            if not self.keep_alive:
                with self.output.exiting_supervisor(supervisor):
                    await supervisor.__aexit__(None, None, None)

    async def _run_tests_then_cancel(
        self,
        supervisor: Supervisor,
        probe_task: asyncio.Task[None],
        current_test: list[Test | None],
    ):
        try:
            await self._run_tests_loop(supervisor, current_test)
        finally:
            _ = probe_task.cancel()

    # --- Test execution ---

    async def _run_tests_loop(
        self,
        supervisor: Supervisor,
        current_test: list[Test | None] | None = None,
    ):
        while self.tests:
            test = self.tests.pop()
            if current_test is not None:
                current_test[0] = test
            async with self._dmesg(supervisor, test):
                async with self._bpftrace(supervisor, test):
                    await self._run_test(supervisor, test)
            if current_test is not None:
                current_test[0] = None

    async def _run_test(self, supervisor: Supervisor, test: Test):
        with self.output.running_test(test) as (stdout, stderr):
            result = await supervisor.run_test(
                test, self.test_timeout, stdout, stderr
            )
        artifact_path = self.output.get_artifact_path(test)
        if artifact_path is not None:
            await supervisor.collect_artifacts(test, artifact_path)
        self.output.finished_test(test, result)

    @asynccontextmanager
    async def _bpftrace(self, supervisor: Supervisor, test: Test):
        if self.bpftrace_command is None:
            yield
            return

        with self.output.log_bpftrace(test) as (stdout, stderr):
            async with supervisor.trace(self.bpftrace_command, stdout, stderr):
                yield

    @asynccontextmanager
    async def _dmesg(self, supervisor: Supervisor, test: Test):
        if not self.dmesg:
            yield
            return

        with self.output.log_dmesg(test) as stdout:
            if stdout is None:
                yield
                return
            async with supervisor.trace("dmesg -W", stdout, None):
                yield

    # --- Probe ---

    async def _probe_loop(self, supervisor: Supervisor):
        while True:
            await asyncio.sleep(self.probe_interval)

            alive = False
            for attempt in range(3):
                if await supervisor.probe():
                    alive = True
                    break
                logger.warning(
                    "probe failed for %s (attempt %d/3)", supervisor, attempt + 1
                )
                if attempt < 2:
                    await asyncio.sleep(1)

            if not alive:
                raise SupervisorExited(repr(supervisor))
