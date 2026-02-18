from pathlib import Path
from unittest.mock import patch

import pytest

from fastfstests.config import RunConfig as Config, MkosiOptions, TestSelectionOptions
from fastfstests.fstests import (
    FSTest,
    collect_tests,
    expand_test,
    get_fstests_path,
    get_tests_from_test_dir,
    parse_exclude_tests_file,
)
from parallelrunner.test import TestStatus


def make_config(
    fstests_path: Path | None = None,
    mkosi_fstests: Path | None = None,
    **selection_kwargs,
) -> Config:
    return Config(
        fstests=fstests_path,
        test_selection=TestSelectionOptions(**selection_kwargs),
        mkosi=MkosiOptions(fstests=mkosi_fstests or Path("/vm/fstests")),
    )


@pytest.fixture
def fstests_dir(tmp_path: Path) -> Path:
    """Create a minimal fstests directory layout."""
    tests = tmp_path / "tests"

    btrfs = tests / "btrfs"
    btrfs.mkdir(parents=True)
    (btrfs / "001").touch()
    (btrfs / "002").touch()
    (btrfs / "003").touch()
    (btrfs / "README").touch()  # non-numeric, should be filtered

    generic = tests / "generic"
    generic.mkdir(parents=True)
    (generic / "001").touch()
    (generic / "002").touch()

    return tmp_path


GROUP_LIST = """\
# comment line
001 auto quick
002 auto stress
003 auto quick dangerous

"""


# --- FSTest.make_result ---


def test_make_result_pass():
    config = make_config(Path("/fstests"))
    test = FSTest("btrfs/001", config)
    result = test.make_result(1.5, 0, b"output", b"")

    assert result.status == TestStatus.PASS
    assert result.name == "btrfs/001"
    assert result.summary is None


def test_make_result_fail():
    config = make_config(Path("/fstests"))
    test = FSTest("btrfs/001", config)
    result = test.make_result(1.5, 1, b"output", b"error")

    assert result.status == TestStatus.FAIL


def test_fstest_retry():
    config = make_config(Path("/fstests"))
    test = FSTest("btrfs/001", config)
    old_id = test.id
    test.retry()

    assert test.name == "btrfs/001"
    assert test.id != old_id


# --- sort_by_duration ---


def test_sort_by_duration(tmp_path: Path):
    from fastfstests.__main__ import sort_by_duration

    # Create latest/ with duration data
    latest = tmp_path / "latest"
    for name, duration in [("btrfs/001", "1.0"), ("btrfs/002", "10.0"), ("btrfs/003", "5.0")]:
        d = latest / name
        d.mkdir(parents=True)
        _ = (d / "duration").write_text(duration)
        _ = (d / "status").write_text("PASS")

    config = make_config(Path("/fstests"))
    tests = [
        FSTest("btrfs/001", config),
        FSTest("btrfs/002", config),
        FSTest("btrfs/003", config),
    ]

    sorted_tests = sort_by_duration(tests, tmp_path)
    names = [t.name for t in sorted_tests]
    # Ascending by duration: pop() will take slowest first
    assert names == ["btrfs/001", "btrfs/003", "btrfs/002"]


def test_sort_by_duration_unknown_tests_last(tmp_path: Path):
    from fastfstests.__main__ import sort_by_duration

    # Only btrfs/001 has duration data
    latest = tmp_path / "latest" / "btrfs" / "001"
    latest.mkdir(parents=True)
    _ = (latest / "duration").write_text("5.0")
    _ = (latest / "status").write_text("PASS")

    config = make_config(Path("/fstests"))
    tests = [
        FSTest("btrfs/001", config),
        FSTest("btrfs/002", config),  # no duration data
    ]

    sorted_tests = sort_by_duration(tests, tmp_path)
    names = [t.name for t in sorted_tests]
    # Unknown (inf) sorts to end, popped first
    assert names == ["btrfs/001", "btrfs/002"]


def test_sort_by_duration_no_latest(tmp_path: Path):
    from fastfstests.__main__ import sort_by_duration

    config = make_config(Path("/fstests"))
    tests = [FSTest("btrfs/001", config), FSTest("btrfs/002", config)]

    sorted_tests = sort_by_duration(tests, tmp_path)
    # No latest/ — returns unchanged
    assert [t.name for t in sorted_tests] == [t.name for t in tests]


def test_make_result_skip():
    config = make_config(Path("/fstests"))
    test = FSTest("btrfs/001", config)
    # Line 7 (0-indexed) contains the skip reason after test name
    lines = [f"line {i}" for i in range(7)]
    lines.append("btrfs/001 needs ext4 filesystem support")
    stdout = "\n".join(lines).encode()
    stdout = stdout.replace(b"line 3", b"[not run]")

    result = test.make_result(1.5, 0, stdout, b"")

    assert result.status == TestStatus.SKIP
    assert result.summary == "needs ext4 filesystem support"


# --- expand_test ---


def test_expand_test_glob(fstests_dir: Path):
    config = make_config(fstests_dir)
    tests = list(expand_test("btrfs/*", config))

    assert sorted(tests) == ["btrfs/001", "btrfs/002", "btrfs/003"]


def test_expand_test_single(fstests_dir: Path):
    config = make_config(fstests_dir)
    tests = list(expand_test("btrfs/001", config))

    assert tests == ["btrfs/001"]


def test_expand_test_filters_non_numeric(fstests_dir: Path):
    config = make_config(fstests_dir)
    tests = list(expand_test("btrfs/*", config))

    assert "btrfs/README" not in tests


# --- parse_exclude_tests_file ---


def test_parse_exclude_tests_file_none():
    config = make_config(Path("/fstests"))
    assert list(parse_exclude_tests_file(config)) == []


def test_parse_exclude_tests_file_reads(tmp_path: Path):
    exclude_file = tmp_path / "exclude.txt"
    exclude_file.write_text("btrfs/001\n# comment\n\nbtrfs/002\n")
    config = make_config(Path("/fstests"), exclude_tests_file=exclude_file)

    tests = list(parse_exclude_tests_file(config))

    assert tests == ["btrfs/001", "btrfs/002"]


def test_parse_exclude_tests_file_missing(tmp_path: Path):
    config = make_config(
        Path("/fstests"), exclude_tests_file=tmp_path / "nonexistent.txt"
    )
    assert list(parse_exclude_tests_file(config)) == []


# --- get_tests_from_test_dir ---


def test_get_tests_from_test_dir_by_group(fstests_dir: Path):
    test_dir = fstests_dir / "tests" / "btrfs"
    with patch("fastfstests.fstests.mkgroupfile", return_value=GROUP_LIST):
        tests = list(get_tests_from_test_dir("quick", test_dir))

    assert sorted(tests) == ["btrfs/001", "btrfs/003"]


def test_get_tests_from_test_dir_all(fstests_dir: Path):
    test_dir = fstests_dir / "tests" / "btrfs"
    with patch("fastfstests.fstests.mkgroupfile", return_value=GROUP_LIST):
        tests = list(get_tests_from_test_dir("all", test_dir))

    assert sorted(tests) == ["btrfs/001", "btrfs/002", "btrfs/003"]


# --- collect_tests ---


def test_collect_tests_from_glob(fstests_dir: Path):
    config = make_config(fstests_dir, tests=["btrfs/*"])
    tests = list(collect_tests(config))

    names = sorted(t.name for t in tests)
    assert names == ["btrfs/001", "btrfs/002", "btrfs/003"]


def test_collect_tests_with_exclusion(fstests_dir: Path):
    config = make_config(
        fstests_dir, tests=["btrfs/*"], exclude_tests=["btrfs/002"]
    )
    tests = list(collect_tests(config))

    names = sorted(t.name for t in tests)
    assert names == ["btrfs/001", "btrfs/003"]


def test_collect_tests_filesystem_filter(fstests_dir: Path):
    config = make_config(
        fstests_dir, tests=["btrfs/*", "generic/*"], file_system="btrfs"
    )
    tests = list(collect_tests(config))

    names = sorted(t.name for t in tests)
    # btrfs tests + generic tests (generic always included)
    assert names == ["btrfs/001", "btrfs/002", "btrfs/003", "generic/001", "generic/002"]


def test_collect_tests_iterate(fstests_dir: Path):
    config = make_config(fstests_dir, tests=["btrfs/001"], iterate=3)
    tests = list(collect_tests(config))

    assert len(tests) == 3
    assert all(t.name == "btrfs/001" for t in tests)


def test_collect_tests_with_group(fstests_dir: Path):
    config = make_config(fstests_dir, groups=["btrfs/quick"])
    with patch("fastfstests.fstests.mkgroupfile", return_value=GROUP_LIST):
        tests = list(collect_tests(config))

    names = sorted(t.name for t in tests)
    assert names == ["btrfs/001", "btrfs/003"]


def test_collect_tests_exclude_group(fstests_dir: Path):
    config = make_config(
        fstests_dir, groups=["btrfs/all"], exclude_groups=["btrfs/dangerous"]
    )
    with patch("fastfstests.fstests.mkgroupfile", return_value=GROUP_LIST):
        tests = list(collect_tests(config))

    names = sorted(t.name for t in tests)
    assert "btrfs/003" not in names
    assert "btrfs/001" in names


# --- error handling ---


def test_get_fstests_path_raises_without_path():
    config = make_config()
    with pytest.raises(ValueError, match="path to fstests not defined"):
        get_fstests_path(config)


def test_fstest_raises_without_mkosi_fstests():
    config = make_config(Path("/fstests"), mkosi_fstests=None)
    config.mkosi.fstests = None
    with pytest.raises(ValueError, match="path to fstests for mkosi not defined"):
        FSTest("btrfs/001", config)


def test_collect_tests_iterate_validation(fstests_dir: Path):
    config = make_config(fstests_dir, tests=["btrfs/001"], iterate=0)
    with pytest.raises(ValueError, match="iterate"):
        list(collect_tests(config))
