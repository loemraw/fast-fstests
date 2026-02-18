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
        OutputOptions(record="my-label", results_dir=None)


def test_record_with_results_dir():
    opts = OutputOptions(record="my-label", results_dir=Path("/tmp/results"))
    assert opts.record == "my-label"


def test_record_none_by_default():
    opts = OutputOptions()
    assert opts.record is None
