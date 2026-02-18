from datetime import datetime

from parallelrunner.test import TestResult, TestStatus


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
