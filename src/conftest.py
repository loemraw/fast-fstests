import shlex
import tomllib
from random import shuffle

import pytest


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
        "--fstests-dir",
        action="store",
        default="/fstests/",
        help="Path to fstests source on vm",
    )
    parser.addini(
        "fstests_dir",
        type="string",
        default=None,
        help="Path to fstests source on vm",
    )

    parser.addoption(
        "--tests",
        action="append",
        default=None,
        help="Which tests to run",
    )
    parser.addini(
        "tests",
        type="args",
        default=None,
        help="List of individual tests to run (can't be used with test-suite)",
    )

    parser.addoption(
        "--test-suite",
        action="store",
        default=None,
        help="Which test suite to run",
        choices=("normal", "no_outliers", "quick"),
    )
    parser.addini(
        "test_suite",
        type="string",
        default=None,
        help="Which test suite to run (can't be used with tests)",
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
def num_machines(request):
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
        options = shlex.split(options)
        print(options)
        return options
    if (options := request.config.getini("mkosi_options")) is not None:
        return " ".join(options)

    return ""


@pytest.fixture(scope="session")
def fstests_dir(request):
    if (dir := request.config.getoption("--fstests-dir")) is not None:
        return dir
    if (dir := request.config.getini("fstests_dir")) is not None:
        return dir

    raise ValueError("fstests-dir not specified!")


@pytest.hookimpl
def pytest_generate_tests(metafunc: pytest.Metafunc):
    suite = metafunc.config.getoption("--test-suite")
    if suite is None:
        suite = metafunc.config.getini("test_suite")

    if isinstance(suite, str):
        with open("suite.toml", "rb") as f:
            tests = tomllib.load(f)[suite]
    else:
        tests = metafunc.config.getoption("--tests")
        if tests is None:
            tests = metafunc.config.getini("tests")

    if tests is None:
        raise ValueError("tests not specified!")

    assert isinstance(tests, list)

    is_random = metafunc.config.getoption("--random")
    if is_random is None:
        is_random = metafunc.config.getini("random")

    if is_random is None:
        raise ValueError("randomness not specified!")

    if is_random:
        shuffle(tests)

    metafunc.parametrize("test", tests)
