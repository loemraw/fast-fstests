import subprocess
from datetime import datetime
from pathlib import Path

from parallelrunner.output import Output
from parallelrunner.test import TestResult, TestStatus


def test_print_exception_string_arg():
    output = Output(results_dir=None)
    output.print_exception(ValueError("something went wrong"))


def test_print_exception_int_arg():
    output = Output(results_dir=None)
    exc = subprocess.CalledProcessError(1, ["mkosi", "build"])
    output.print_exception(exc)


def test_print_exception_empty_args():
    output = Output(results_dir=None)
    output.print_exception(ValueError())


def test_print_exception_with_notes():
    output = Output(results_dir=None)
    exc = RuntimeError("machine failed")
    exc.add_note("make sure to build your image first")
    exc.add_note("mkosi config path: /home/user/mkosi-kernel")
    output.print_exception(exc)


def test_print_exception_group():
    output = Output(results_dir=None)
    exc = ExceptionGroup("spawn failures", [
        ValueError("supervisor 1 failed"),
        RuntimeError("supervisor 2 timed out"),
    ])
    output.print_exception(exc)


def test_record_crash_reschedule_tracks_count(tmp_path: Path):
    output = Output(results_dir=tmp_path)

    class FakeTest:
        name = "btrfs/001"
        id = "2025-01-01_00-00-00_000001"

    result = TestResult.from_error("btrfs/001", "test-id", "supervisor died", 0.0, datetime.now())
    output.record_crash_reschedule(FakeTest(), result)  # type: ignore[arg-type]

    assert output._crash_reschedules == {"btrfs/001": 1}
    assert len(output._results) == 0  # not in main results


def test_record_crash_reschedule_increments(tmp_path: Path):
    output = Output(results_dir=tmp_path)

    class FakeTest:
        name = "btrfs/001"
        id = "2025-01-01_00-00-00_000001"

    result = TestResult.from_error("btrfs/001", "test-id", "supervisor died", 0.0, datetime.now())
    output.record_crash_reschedule(FakeTest(), result)  # type: ignore[arg-type]
    output.record_crash_reschedule(FakeTest(), result)  # type: ignore[arg-type]

    assert output._crash_reschedules == {"btrfs/001": 2}
