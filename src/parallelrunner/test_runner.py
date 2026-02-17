import asyncio
import logging
import sys
from collections.abc import Iterable
from contextlib import asynccontextmanager
from pathlib import Path

from .output import Output
from .supervisor import Supervisor, SupervisorExited
from .test import Test

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
    ):
        self.tests: list[Test] = list(tests)
        self.supervisors: list[Supervisor] = list(supervisors)
        self.output: Output = output
        self.keep_alive: bool = keep_alive
        self.test_timeout: int | None = test_timeout
        self.probe_interval: int = probe_interval

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
            async with self.__parallel_supervisor_cm():
                with self.output.running_tests(len(self.tests)):
                    async with asyncio.TaskGroup() as tg:
                        for supervisor in self.supervisors:
                            _ = tg.create_task(self.__worker(supervisor))

                if self.keep_alive:
                    with self.output.keeping_alive():
                        while True:
                            await asyncio.sleep(1)
        finally:
            self.output.print_summary()

    async def __worker(self, supervisor: Supervisor):
        if self.probe_interval > 0:
            try:
                async with asyncio.TaskGroup() as tg:
                    probe_task = tg.create_task(self.__probe_loop(supervisor))

                    async def run_tests():
                        try:
                            await self.__run_tests_loop(supervisor)
                        finally:
                            _ = probe_task.cancel()

                    _ = tg.create_task(run_tests())
            except* SupervisorExited:
                pass
        else:
            await self.__run_tests_loop(supervisor)

    async def __run_tests_loop(self, supervisor: Supervisor):
        async def run_test(test: Test):
            with self.output.running_test(test) as (stdout, stderr):
                result = await supervisor.run_test(
                    test, self.test_timeout, stdout, stderr
                )
            self.output.finished_test(test, result)

        @asynccontextmanager
        async def bpftrace(test: Test):
            if self.bpftrace_command is None:
                yield
                return

            with self.output.log_bpftrace(test) as (stdout, stderr):
                async with supervisor.trace(self.bpftrace_command, stdout, stderr):
                    yield

        while self.tests:
            test = self.tests.pop()
            async with bpftrace(test):
                await run_test(test)

    async def __probe_loop(self, supervisor: Supervisor):
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
                self.output.supervisor_died(supervisor)
                raise SupervisorExited(repr(supervisor))

    @asynccontextmanager
    async def __parallel_supervisor_cm(self):
        await self.__spawn_supervisors()

        try:
            yield
        finally:
            await self.__cleanup_supervisors()

    async def __spawn_supervisors(self):
        async def spawn_supervisor(supervisor: Supervisor):
            with self.output.spawning_supervisor(supervisor):
                return await supervisor.__aenter__()

        with self.output.spawning_supervisors(len(self.supervisors)):
            async with asyncio.TaskGroup() as tg:
                for supervisor in self.supervisors:
                    _ = tg.create_task(spawn_supervisor(supervisor))

    async def __cleanup_supervisors(self):
        async def supervisor_exit(supervisor: Supervisor):
            with self.output.cleaning_supervisor(supervisor):
                await supervisor.__aexit__(*sys.exc_info())

        with self.output.cleaning_supervisors(len(self.supervisors)):
            for supervisor in self.supervisors:
                await supervisor_exit(supervisor)
