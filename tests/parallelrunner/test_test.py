from datetime import datetime
from typing import override

from parallelrunner.test import Test, TestResult, TestStatus


def test_from_error():
    result = TestResult.from_error("btrfs/001", "timed out", 30.0, datetime(2025, 1, 1))

    assert result.name == "btrfs/001"
    assert result.status == TestStatus.ERROR
    assert result.duration == 30.0
    assert result.summary == "timed out"
    assert result.retcode is None
    assert result.stdout is None
    assert result.stderr is None


def test_status_enum_values():
    assert TestStatus.PASS != TestStatus.FAIL
    assert TestStatus.SKIP != TestStatus.ERROR
    assert len(TestStatus) == 4


class ConcreteTest(Test):
    @override
    def make_result(self, duration: float, retcode: int, stdout: bytes, stderr: bytes) -> TestResult:
        return TestResult(self.name, TestStatus.PASS, duration, datetime.now(), None, retcode, stdout, stderr)


def test_retry_creates_new_id():
    test = ConcreteTest("btrfs/001", "echo test")
    old_id = test.id
    test.retry()

    assert test.name == "btrfs/001"
    assert test.test_cmd == "echo test"
    assert test.id != old_id
