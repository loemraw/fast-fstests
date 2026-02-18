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


def test_regressions_without_results_dir():
    with pytest.raises(ValueError, match="--print-test-regressions requires --results-dir"):
        OutputOptions(print_test_regressions=5, results_dir=None)


def test_regressions_negative():
    with pytest.raises(ValueError, match="--print-test-regressions must be >= 0"):
        OutputOptions(print_test_regressions=-1, results_dir=Path("/tmp/results"))


def test_regressions_with_results_dir():
    opts = OutputOptions(print_test_regressions=5, results_dir=Path("/tmp/results"))
    assert opts.print_test_regressions == 5
