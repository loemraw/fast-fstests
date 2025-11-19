import asyncio
import sys
from collections.abc import Iterable
from contextlib import asynccontextmanager

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
    ):
        self.tests: list[Test] = list(tests)
        self.supervisors: list[Supervisor] = list(supervisors)
        self.output: Output = output
        self.keep_alive: bool = keep_alive
        self.test_timeout: int | None = test_timeout

    async def run(self):
        try:
            async with self._parallel_supervisor_cm():
                with self.output.running_tests(len(self.tests)):
                    async with asyncio.TaskGroup() as tg:
                        for supervisor in self.supervisors:
                            _ = tg.create_task(self._worker(supervisor))

                if self.keep_alive:
                    with self.output.keeping_alive():
                        while True:
                            await asyncio.sleep(1)
        finally:
            self.output.print_summary()

    async def _worker(self, supervisor: Supervisor):
        runner = supervisor.run_tests(self.test_timeout)
        _ = await anext(runner)
        while self.tests:
            test = self.tests.pop()
            with self.output.running_test(test):
                await runner.asend(test)

    @asynccontextmanager
    async def _parallel_supervisor_cm(self):
        await self._spawn_supervisors()

        try:
            yield
        finally:
            await self._cleanup_supervisors()

    async def _spawn_supervisors(self):
        async def spawn_supervisor(supervisor: Supervisor):
            with self.output.spawning_supervisor(supervisor):
                return await supervisor.__aenter__()

        with self.output.spawning_supervisors(len(self.supervisors)):
            async with asyncio.TaskGroup() as tg:
                for supervisor in self.supervisors:
                    _ = tg.create_task(spawn_supervisor(supervisor))

    async def _cleanup_supervisors(self):
        async def supervisor_exit(supervisor: Supervisor):
            with self.output.cleaning_supervisor(supervisor):
                await supervisor.__aexit__(*sys.exc_info())

        with self.output.cleaning_supervisors(len(self.supervisors)):
            for supervisor in self.supervisors:
                await supervisor_exit(supervisor)
