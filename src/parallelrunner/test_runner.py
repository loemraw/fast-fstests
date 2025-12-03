import asyncio
import sys
from collections.abc import Iterable
from contextlib import asynccontextmanager
from pathlib import Path

from .output import Output
from .supervisor import Supervisor
from .test import Test


class TestRunner:
    def __init__(
        self,
        tests: Iterable[Test],
        supervisors: Iterable[Supervisor],
        output: Output,
        keep_alive: bool = False,
        test_timeout: int | None = None,
        bpftrace: str | Path | None = None,
    ):
        self.tests: list[Test] = list(tests)
        self.supervisors: list[Supervisor] = list(supervisors)
        self.output: Output = output
        self.keep_alive: bool = keep_alive
        self.test_timeout: int | None = test_timeout

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
