from pathlib import Path

import pytest

from fastfstests.config import OutputOptions, hbh


def test_hbh_default():
    assert hbh("42") == "(default: 42)"


def test_hbh_empty():
    assert hbh("") == ""


def test_hbh_none():
    assert hbh("None") == ""


def test_hbh_empty_quotes():
    assert hbh("''") == ""


def test_verbose_without_results_dir():
    with pytest.raises(ValueError, match="--verbose requires --results-dir"):
        OutputOptions(verbose=True, results_dir=None)


def test_verbose_with_results_dir():
    opts = OutputOptions(verbose=True, results_dir=Path("/tmp/results"))
    assert opts.verbose is True


def test_record_without_results_dir():
    with pytest.raises(ValueError, match="--record requires --results-dir"):
        OutputOptions(record=True, results_dir=None)


def test_diff_without_results_dir():
    with pytest.raises(ValueError, match="--diff requires --results-dir"):
        OutputOptions(diff=True, results_dir=None)


def test_record_with_results_dir():
    opts = OutputOptions(record=True, results_dir=Path("/tmp/results"))
    assert opts.record is True


def test_diff_with_results_dir():
    opts = OutputOptions(diff=True, results_dir=Path("/tmp/results"))
    assert opts.diff is True
