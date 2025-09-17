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
    ):
        self.tests: list[Test] = list(tests)
        self.supervisors: list[Supervisor] = list(supervisors)
        self.output: Output = output
        self.keep_alive: bool = keep_alive

    async def run(self):
        async with self._parallel_supervisor_cm():
            with self.output.running_tests(len(self.tests)):
                async with asyncio.TaskGroup() as tg:
                    for supervisor in self.supervisors:
                        _ = tg.create_task(self._worker(supervisor))

            if self.keep_alive:
                with self.output.keeping_alive():
                    while True:
                        await asyncio.sleep(1)

    async def _worker(self, supervisor: Supervisor):
        runner = supervisor.run_tests()
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
        with self.output.spawning_supervisors(len(self.supervisors)):
            async with asyncio.TaskGroup() as tg:
                for supervisor in self.supervisors:
                    _ = tg.create_task(self._spawn_supervisor(supervisor))

    async def _spawn_supervisor(self, supervisor: Supervisor):
        with self.output.spawning_supervisor(supervisor):
            return await supervisor.__aenter__()

    async def _cleanup_supervisors(self):
        with self.output.cleaning_supervisors(len(self.supervisors)):
            exc_type, exc_value, traceback = sys.exc_info()

            async def supervisor_exit(supervisor: Supervisor):
                with self.output.cleaning_supervisor(supervisor):
                    await supervisor.__aexit__(exc_type, exc_value, traceback)

            _ = await asyncio.gather(
                *[supervisor_exit(supervisor) for supervisor in self.supervisors]
            )
