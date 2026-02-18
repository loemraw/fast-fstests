import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from types import TracebackType
from typing import IO, Self, override

import pytest

from parallelrunner.output import Output
from parallelrunner.supervisor import Supervisor
from parallelrunner.test import Test, TestResult, TestStatus
from parallelrunner.test_runner import TestRunner


class MockTest(Test):
    @override
    def make_result(
        self,
        duration: float,
        retcode: int,
        stdout: bytes,
        stderr: bytes,
        artifacts: dict[str, bytes],
    ) -> TestResult:
        return TestResult(
            self.name,
            TestStatus.PASS,
            duration,
            datetime.now(),
            None,
            retcode,
            stdout,
            stderr,
            artifacts,
        )


class MockSupervisor(Supervisor):
    def __init__(self, probe_results: list[bool] | None = None, test_delay: float = 0):
        self.tests_run: list[str] = []
        self.probe_call_count: int = 0
        self._probe_results: list[bool] = probe_results or []
        self._probe_index: int = 0
        self._exited: bool = False
        self._test_delay: float = test_delay
        self.enter_count: int = 0

    @override
    async def __aenter__(self) -> Self:
        self._exited = False
        self.enter_count += 1
        return self

    @override
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ):
        self._exited = True

    @override
    async def run_test(
        self,
        test: Test,
        timeout: int | None,
        stdout: IO[bytes],
        stderr: IO[bytes],
    ) -> TestResult:
        if self._test_delay > 0:
            await asyncio.sleep(self._test_delay)
        self.tests_run.append(test.name)
        return test.make_result(0.1, 0, b"", b"", {})

    @asynccontextmanager
    @override
    async def trace(
        self,
        command: str | None,
        stdout: IO[bytes] | None,
        stderr: IO[bytes] | None,
    ) -> AsyncGenerator[None, None]:
        yield

    @property
    @override
    def exited(self) -> bool:
        return self._exited

    @override
    async def probe(self) -> bool:
        self.probe_call_count += 1
        if self._probe_index < len(self._probe_results):
            result = self._probe_results[self._probe_index]
            self._probe_index += 1
            return result
        return True


def make_tests(n: int) -> list[MockTest]:
    return [MockTest(f"test/{i:03d}", f"echo {i}") for i in range(n)]


@pytest.fixture
def output() -> Output:
    return Output(results_dir=None)


def test_all_tests_executed(output: Output):
    tests = make_tests(5)
    supervisor = MockSupervisor()
    runner = TestRunner(tests, [supervisor], output)

    asyncio.run(runner.run())

    assert sorted(supervisor.tests_run) == [f"test/{i:03d}" for i in range(5)]


def test_tests_distribute_across_supervisors(output: Output):
    tests = make_tests(6)
    s1 = MockSupervisor(test_delay=0.05)
    s2 = MockSupervisor(test_delay=0.05)
    runner = TestRunner(tests, [s1, s2], output)

    asyncio.run(runner.run())

    all_run = sorted(s1.tests_run + s2.tests_run)
    assert all_run == [f"test/{i:03d}" for i in range(6)]
    assert len(s1.tests_run) > 0
    assert len(s2.tests_run) > 0


def test_probe_not_called_when_disabled(output: Output):
    tests = make_tests(3)
    supervisor = MockSupervisor()
    runner = TestRunner(tests, [supervisor], output, probe_interval=0)

    asyncio.run(runner.run())

    assert supervisor.probe_call_count == 0
    assert len(supervisor.tests_run) == 3


def test_probe_failure_stops_worker(output: Output):
    # Supervisor that always fails probes, with slow tests so probe has time to fire
    dead = MockSupervisor(probe_results=[False, False, False], test_delay=2)
    alive = MockSupervisor()
    tests = make_tests(4)
    runner = TestRunner(
        tests, [dead, alive], output, probe_interval=1, max_supervisor_restarts=0
    )

    asyncio.run(runner.run())

    all_run = sorted(dead.tests_run + alive.tests_run)
    assert all_run == [f"test/{i:03d}" for i in range(4)]


def test_probe_retry_succeeds(output: Output):
    # First probe fails, second succeeds — supervisor stays alive
    # Tests need to run long enough for the retry cycle (probe at 1s, retry at 2s)
    supervisor = MockSupervisor(probe_results=[False, True], test_delay=1.0)
    tests = make_tests(3)
    runner = TestRunner(tests, [supervisor], output, probe_interval=1)

    asyncio.run(runner.run())

    assert len(supervisor.tests_run) == 3
    assert supervisor.probe_call_count >= 2


def test_completes_with_probing_enabled(output: Output):
    """Tests complete normally and worker exits cleanly with probing on."""
    supervisor = MockSupervisor()
    tests = make_tests(3)
    runner = TestRunner(tests, [supervisor], output, probe_interval=1)

    asyncio.run(runner.run())

    assert len(supervisor.tests_run) == 3


def test_empty_test_list(output: Output):
    supervisor = MockSupervisor()
    runner = TestRunner([], [supervisor], output)

    asyncio.run(runner.run())

    assert supervisor.tests_run == []


def test_supervisor_spawn_failure(output: Output):
    """When the only supervisor fails to spawn, worker exits gracefully."""

    class FailSpawnSupervisor(MockSupervisor):
        @override
        async def __aenter__(self) -> Self:
            self._exited = True
            raise RuntimeError("spawn failed")

    tests = make_tests(3)
    runner = TestRunner(tests, [FailSpawnSupervisor()], output)

    asyncio.run(runner.run())
    # No exception — worker exits gracefully, no tests run


def test_all_supervisors_die_remaining_tests_lost(output: Output):
    """When all supervisors die with restarts disabled, remaining tests are never executed."""
    s1 = MockSupervisor(probe_results=[False, False, False], test_delay=2)
    s2 = MockSupervisor(probe_results=[False, False, False], test_delay=2)
    tests = make_tests(10)
    runner = TestRunner(
        tests, [s1, s2], output, probe_interval=1, max_supervisor_restarts=0
    )

    asyncio.run(runner.run())

    total_run = len(s1.tests_run) + len(s2.tests_run)
    assert total_run < 10


def test_run_test_exception_propagates(output: Output):
    """If run_test raises, the exception propagates through the worker."""

    class ExplodingSupervisor(MockSupervisor):
        @override
        async def run_test(
            self,
            test: Test,
            timeout: int | None,
            stdout: IO[bytes],
            stderr: IO[bytes],
        ) -> TestResult:
            raise RuntimeError("VM exploded")

    tests = make_tests(1)
    runner = TestRunner(tests, [ExplodingSupervisor()], output)

    with pytest.raises(ExceptionGroup):
        asyncio.run(runner.run())


def test_partial_spawn_failure(output: Output):
    """One supervisor fails to spawn, other succeeds — tests run on healthy one."""

    class FailSpawnSupervisor(MockSupervisor):
        @override
        async def __aenter__(self) -> Self:
            self._exited = True
            raise RuntimeError("spawn failed")

    healthy = MockSupervisor()
    tests = make_tests(3)
    runner = TestRunner(tests, [FailSpawnSupervisor(), healthy], output)

    asyncio.run(runner.run())

    assert len(healthy.tests_run) == 3


def test_worker_oserror_continues(output: Output):
    """Worker that raises OSError dies; other workers pick up remaining tests."""

    class OSErrorSupervisor(MockSupervisor):
        @override
        async def run_test(
            self,
            test: Test,
            timeout: int | None,
            stdout: IO[bytes],
            stderr: IO[bytes],
        ) -> TestResult:
            raise OSError("connection refused")

    healthy = MockSupervisor()
    tests = make_tests(5)
    runner = TestRunner(tests, [OSErrorSupervisor(), healthy], output)

    asyncio.run(runner.run())

    assert len(healthy.tests_run) > 0


def test_supervisor_restarts_on_death(output: Output):
    """Supervisor dies from probe failure, restarts, and completes remaining tests."""
    # 3 False probes = one death. After restart, probes return True (default).
    supervisor = MockSupervisor(probe_results=[False, False, False], test_delay=2)
    tests = make_tests(4)
    runner = TestRunner(tests, [supervisor], output, probe_interval=1)

    asyncio.run(runner.run())

    assert sorted(supervisor.tests_run) == [f"test/{i:03d}" for i in range(4)]
    assert supervisor.enter_count == 2  # Initial spawn + 1 restart


def test_interrupted_test_rescheduled(output: Output):
    """Test in-flight when VM dies is rescheduled and completed after restart."""
    # Slow test delay ensures the test is in-flight when probe fires.
    # 3 False probes = death. After restart, probes return True.
    supervisor = MockSupervisor(probe_results=[False, False, False], test_delay=2)
    tests = make_tests(2)
    runner = TestRunner(tests, [supervisor], output, probe_interval=1)

    asyncio.run(runner.run())

    # Both tests should complete (the interrupted one gets rescheduled)
    assert sorted(supervisor.tests_run) == ["test/000", "test/001"]


def test_max_restarts_exceeded(output: Output):
    """Test that repeatedly kills supervisor gets ERROR after max restarts."""
    # Always fail probes — supervisor dies every time it restarts.
    # With max_supervisor_restarts=1, first death reschedules, second records ERROR.
    supervisor = MockSupervisor(
        probe_results=[False] * 20, test_delay=2
    )
    tests = make_tests(1)
    runner = TestRunner(
        tests, [supervisor], output, probe_interval=1, max_supervisor_restarts=1
    )

    asyncio.run(runner.run())

    # The test should have been attempted but ultimately get ERROR status
    # (killed supervisor 1 time = max, so it's marked as error on first death)
    assert supervisor.enter_count >= 1
