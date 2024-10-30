import tomllib
from random import shuffle

import pytest


@pytest.hookimpl
def pytest_addoption(parser: pytest.Parser):
    parser.addoption(
        "--num-machines",
        action="store",
        default="5",
        help="Number of virtual machines to use for testing",
    )

    parser.addoption(
        "--mkosi-kernel-dir",
        action="store",
        default="/home/leomar/local/mkosi-kernel/",
        help="Path to mkosi-kernel",
    )

    parser.addoption(
        "--fstests-dir",
        action="store",
        default="/fstests/",
        help="Path to fstests on machines",
    )

    parser.addoption(
        "-T",
        action="append",
        default=[],
        help="Which tests to run",
    )

    parser.addoption(
        "--test-suite",
        action="store",
        default=None,
        help="Which test suite to run",
        choices=("normal", "no_outliers", "quick"),
    )

    parser.addoption(
        "-R",
        action="store_true",
        help="Randomize order of tests",
    )


@pytest.fixture(scope="session")
def num_machines(request):
    return int(request.config.getoption("--num-machines"))


@pytest.fixture(scope="session")
def mkosi_kernel_dir(request):
    return request.config.getoption("--mkosi-kernel-dir")


@pytest.fixture(scope="session")
def fstests_dir(request):
    return request.config.getoption("--fstests-dir")


@pytest.hookimpl
def pytest_generate_tests(metafunc: pytest.Metafunc):
    if suite := metafunc.config.getoption("--test-suite"):
        with open("config.toml", "rb") as f:
            tests = tomllib.load(f)["suite"][suite]
    else:
        tests = metafunc.config.getoption("-T")

    assert isinstance(tests, list)

    if metafunc.config.getoption("-R"):
        shuffle(tests)

    metafunc.parametrize("test", tests)
