import os
import subprocess
from pathlib import Path

import pytest

from parallelrunner.output import BaselineResult, Output
from parallelrunner.test import TestStatus


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


# --- Baseline tests ---


@pytest.fixture
def baseline_dir(tmp_path: Path) -> Path:
    """Create a baseline directory with test results."""
    baseline = tmp_path / "baseline"

    btrfs_001 = baseline / "btrfs" / "001"
    btrfs_001.mkdir(parents=True)
    _ = (btrfs_001 / "status").write_text("PASS")
    _ = (btrfs_001 / "duration").write_text("5.0")

    btrfs_002 = baseline / "btrfs" / "002"
    btrfs_002.mkdir(parents=True)
    _ = (btrfs_002 / "status").write_text("FAIL")
    _ = (btrfs_002 / "duration").write_text("3.0")

    return tmp_path


def test_load_baseline(baseline_dir: Path):
    output = Output(results_dir=baseline_dir, diff=True)

    assert "btrfs/001" in output._baseline
    assert output._baseline["btrfs/001"].status == TestStatus.PASS
    assert output._baseline["btrfs/001"].duration == 5.0

    assert "btrfs/002" in output._baseline
    assert output._baseline["btrfs/002"].status == TestStatus.FAIL
    assert output._baseline["btrfs/002"].duration == 3.0


def test_load_baseline_missing_errors():
    with pytest.raises(ValueError, match="no baseline found"):
        Output(results_dir=Path("/nonexistent"), diff=True)


def test_save_baseline(tmp_path: Path):
    results_dir = tmp_path / "results"
    latest = results_dir / "latest" / "btrfs" / "001"
    latest.mkdir(parents=True)
    _ = (latest / "status").write_text("PASS")
    _ = (latest / "duration").write_text("2.5")

    output = Output(results_dir=results_dir, record=True)
    output._save_baseline()

    baseline = results_dir / "baseline" / "btrfs" / "001"
    assert baseline.exists()
    assert (baseline / "status").read_text() == "PASS"


def test_diff_no_baseline_disabled():
    """diff=False should not try to load baseline."""
    output = Output(results_dir=None, diff=False)
    assert output._baseline == {}


def test_format_diff_new_test():
    output = Output(results_dir=None)
    output._baseline = {}

    from datetime import datetime

    from parallelrunner.test import TestResult, TestStatus
    result = TestResult("btrfs/999", TestStatus.PASS, 1.0, datetime.now(), None, 0, None, None)
    assert "new" in output._format_diff(result)


def test_format_diff_status_change(baseline_dir: Path):
    output = Output(results_dir=baseline_dir, diff=True)

    from datetime import datetime

    from parallelrunner.test import TestResult
    # btrfs/001 was PASS in baseline, now FAIL
    result = TestResult("btrfs/001", TestStatus.FAIL, 5.0, datetime.now(), None, 1, None, None)
    diff_str = output._format_diff(result)
    assert "pass" in diff_str.lower()
    assert "fail" in diff_str.lower()


def test_format_diff_duration_change(baseline_dir: Path):
    output = Output(results_dir=baseline_dir, diff=True)

    from datetime import datetime

    from parallelrunner.test import TestResult
    # btrfs/001 was 5.0s, now 8.0s
    result = TestResult("btrfs/001", TestStatus.PASS, 8.0, datetime.now(), None, 0, None, None)
    diff_str = output._format_diff(result)
    assert "+3s" in diff_str
