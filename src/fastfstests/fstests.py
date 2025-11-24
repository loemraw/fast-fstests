import glob
import logging
import random
import subprocess
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import override

from parallelrunner import Test, TestResult, TestStatus

from .config import Config

logger = logging.getLogger(__name__)


class FSTest(Test):
    def __init__(self, name: str, config: Config):
        assert (mkosi_fstests := config.mkosi.fstests) is not None, (
            "path to fstests for mkosi not defined"
        )

        check_options: list[str] = []
        if (section := config.test_selection.section) is not None:
            check_options.extend(("-s", section))
        if (section := config.test_selection.exclude_section) is not None:
            check_options.extend(("-S", section))

        test = f"cd {str(mkosi_fstests)}; ./check {' '.join(check_options)} {name}"
        super().__init__(name, test, [mkosi_fstests.joinpath(f"results/*/{name}*")])

    @override
    def set_result(
        self,
        duration: float,
        retcode: int,
        stdout: bytes,
        stderr: bytes,
        artifacts: dict[str, bytes],
    ):
        match retcode:
            case 0:
                if b"[not run]" in stdout:
                    status = TestStatus.SKIP
                else:
                    status = TestStatus.PASS
            case _:
                status = TestStatus.FAIL

        summary = " ".join(stdout.decode().splitlines()[7].split()[1:])
        self.result: TestResult | None = TestResult(
            status,
            self.name,
            duration,
            datetime.now(),
            summary,
            retcode,
            stdout,
            stderr,
            artifacts,
        )


def assert_fstests(config: Config) -> Path:
    assert config.fstests is not None, "path to fstests not defined"
    return config.fstests


def expand_test(test: str, config: Config) -> Iterable[str]:
    fstests = assert_fstests(config)
    test_path = fstests.joinpath("tests", test)
    tests: list[str] = list(
        filter(
            lambda x: Path(x).name.isdigit(),
            glob.glob(str(test_path)),
        )
    )
    return [str(Path(test).relative_to(fstests.joinpath("tests"))) for test in tests]


def parse_exclude_tests_file(config: Config) -> Iterable[str]:
    if (exclude_tests_file := config.test_selection.exclude_tests_file) is None:
        return []

    tests: list[str] = []
    try:
        with open(exclude_tests_file, "r") as f:
            for test in f.readlines():
                if len(test := test.strip()) == 0 or test[0] == "#":
                    continue
                tests.append(test)
    except FileNotFoundError:
        logger.exception("exclude tests file not found")

    return tests


def get_tests_for_group(group: str, config: Config) -> Iterable[str]:
    fstests = assert_fstests(config)

    if "/" in group:
        test_dir, group = group.split("/")
        # read tests from subdir and select group
        return get_tests_from_test_dir(group, fstests.joinpath("tests", test_dir))

    tests: set[str] = set()
    for test_dir in fstests.joinpath("tests").iterdir():
        if test_dir.is_dir():
            tests.update(get_tests_from_test_dir(group, test_dir))

    return tests


def get_tests_from_test_dir(group: str, test_dir: Path) -> Iterable[str]:
    tests: list[str] = []
    group_file = mkgroupfile(test_dir)
    for line in group_file.splitlines():
        if len(line := line.strip()) == 0 or line[0] == "#":
            continue
        groups = line.split(" ")
        test_name = groups[0]
        groups = groups[1:]

        if group == "all" or group in groups:
            tests.append(f"{test_dir.name}/{test_name}")
    return tests


def mkgroupfile(test_dir: Path) -> str:
    proc = subprocess.run(
        ["../../tools/mkgroupfile"], cwd=test_dir, capture_output=True
    )
    if proc.returncode == 0:
        return proc.stdout.decode()

    logger.warning("mkgroupfile non-zero return code: %d", proc.returncode)
    try:
        with open(test_dir.joinpath("group.list"), "r") as f:
            return f.read()
    except FileNotFoundError:
        logger.exception("could not find group.list")

    return ""


def collect_tests(config: Config) -> Iterable[Test]:
    tests = set[str]()

    for test in config.test_selection.tests:
        tests.update(expand_test(test, config))
    for group in config.test_selection.groups:
        tests.update(get_tests_for_group(group, config))

    for test in config.test_selection.exclude_tests:
        tests.difference_update(expand_test(test, config))
    for test in parse_exclude_tests_file(config):
        tests.difference_update(expand_test(test, config))
    for group in config.test_selection.exclude_groups:
        tests.difference_update(get_tests_for_group(group, config))

    tests = list(tests)

    if (file_system := config.test_selection.file_system) is not None:
        prev_tests = len(tests)
        tests = [test for test in tests if file_system in test or "generic" in test]
        if not tests and prev_tests:
            logger.warning("no tests match your specified file system: %s", file_system)

    assert config.test_selection.iterate >= 1, (
        "test_selection iterate value must be greater than or equal to 1"
    )
    if config.test_selection.iterate > 1:
        tests = [test for test in tests for _ in range(config.test_selection.iterate)]

    if config.test_selection.randomize:
        random.shuffle(tests)
    else:
        tests = reversed(sorted(tests))

    return [FSTest(test, config) for test in tests]
