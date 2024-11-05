import logging
import subprocess
from random import shuffle

import pytest

logger = logging.getLogger(__name__)


@pytest.hookimpl
def pytest_addoption(parser: pytest.Parser):
    parser.addoption(
        "--num-machines",
        action="store",
        default=None,
        help="Number of virtual machines to use for testing",
    )
    parser.addini(
        "num_machines",
        type="string",
        default=None,
        help="Number of virtual machines to use for testing",
    )

    parser.addoption(
        "--mkosi-kernel-dir",
        action="store",
        default=None,
        help="Path to mkosi-kernel",
    )
    parser.addini(
        "mkosi_kernel_dir",
        type="string",
        default=None,
        help="Path to mkosi-kernel",
    )

    parser.addoption(
        "--mkosi-options",
        action="store",
        default=None,
        help="Options to pass to mkosi",
    )
    parser.addini(
        "mkosi_options",
        type="linelist",
        default=None,
        help="Options to pass to mkosi",
    )

    parser.addoption(
        "--fstests-dir-host",
        action="store",
        default=None,
        help="Path to fstests source on vm",
    )
    parser.addini(
        "fstests_dir_host",
        type="string",
        default=None,
        help="Path to fstests source on vm",
    )

    parser.addoption(
        "--fstests-dir-machine",
        action="store",
        default=None,
        help="Path to fstests source on vm",
    )
    parser.addini(
        "fstests_dir_machine",
        type="string",
        default=None,
        help="Path to fstests source on vm",
    )

    parser.addoption(
        "--tests",
        action="append",
        default=None,
        help="List of individual tests to run (can't be used with group)",
    )
    parser.addini(
        "tests",
        type="args",
        default=None,
        help="List of individual tests to run (can't be used with group)",
    )

    parser.addoption(
        "--except",
        action="append",
        default=None,
        help="List of individual tests to not run",
    )
    parser.addini(
        "except",
        type="args",
        default=None,
        help="List of individual tests to not run",
    )

    parser.addoption(
        "--group",
        action="store",
        default=None,
        help="Which group to run (can't be used with tests)",
    )
    parser.addini(
        "group",
        type="string",
        default=None,
        help="Which group to run (can't be used with tests)",
    )

    parser.addoption(
        "--random",
        action="store_true",
        help="Randomize order of tests",
    )
    parser.addini(
        "random",
        type="bool",
        default=False,
        help="Randomize order of tests",
    )


@pytest.fixture(scope="session")
def num_machines(request: pytest.FixtureRequest):
    if (nm := request.config.getoption("--num-machines")) is not None:
        return int(nm)
    if (nm := request.config.getini("num_machines")) is not None:
        return int(nm)

    raise ValueError("num-machines not specified!")


@pytest.fixture(scope="session")
def mkosi_kernel_dir(request):
    if (dir := request.config.getoption("--mkosi-kernel-dir")) is not None:
        return dir
    if (dir := request.config.getini("mkosi_kernel_dir")) is not None:
        return dir

    raise ValueError("mkosi-kernel-dir not specified!")


@pytest.fixture(scope="session")
def mkosi_options(request):
    if (options := request.config.getoption("--mkosi-options")) is not None:
        return options
    if (options := request.config.getini("mkosi_options")) is not None:
        return " ".join(options)

    return ""


@pytest.fixture(scope="session")
def fstests_dir(request):
    dir = request.config.getoption(
        "--fstests-dir-machine"
    ) or request.config.getini("fstests_dir_machine")
    if dir is None:
        raise ValueError("fstests-dir-machine not specified!")
    return dir


def get_tests_for_(group, except_tests, fstests_dir_host):
    def tests_for_(dir, group):
        proc = subprocess.run(
            "../../tools/mkgroupfile",
            cwd=f"{fstests_dir_host}/tests/{dir}",
            capture_output=True,
        )

        if proc.returncode != 0:
            raise ValueError("unable to determine tests")

        stdout = proc.stdout.decode()

        for line in stdout.splitlines():
            if group in line:
                test = f"{dir}/{line.split()[0]}"
                if test in except_tests:
                    continue
                yield test

    if "/" in group:
        fs_dir, group = group.split("/")
        return [
            *tests_for_(fs_dir, group),
        ]

    return [
        *tests_for_("btrfs", group),
        *tests_for_("generic", group),
    ]


@pytest.hookimpl
def pytest_generate_tests(metafunc: pytest.Metafunc):
    group = metafunc.config.getoption("--group")
    tests = metafunc.config.getoption("--tests")

    if group is None and tests is None:
        group = metafunc.config.getini("group")
        tests = metafunc.config.getini("tests")

        if group is None and tests is None:
            raise ValueError("no tests specified!")

    if group and tests:
        raise ValueError("cannot specify both suite and tests!")

    if isinstance(group, str):
        fstests_dir_host = metafunc.config.getoption(
            "--fstests-dir-host"
        ) or metafunc.config.getini("fstests_dir_host")

        if not isinstance(fstests_dir_host, str):
            raise ValueError("fstests_dir_host not specified!")

        except_tests = metafunc.config.getoption(
            "--except"
        ) or metafunc.config.getini("except")

        tests = get_tests_for_(group, except_tests, fstests_dir_host)

    assert isinstance(tests, list)

    is_random = metafunc.config.getoption("--random")
    if is_random is None:
        is_random = metafunc.config.getini("random")

    if is_random is None:
        raise ValueError("randomness not specified!")

    if is_random:
        shuffle(tests)

    metafunc.parametrize("test", tests)


@pytest.hookimpl
def pytest_configure(config: pytest.Config):
    logging.basicConfig()
