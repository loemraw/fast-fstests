import json
import logging
import os
import pickle
import random
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import List, Union

import pytest
from filelock import FileLock
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

"""
PYTEST OPTIONS
"""


@pytest.hookimpl
def pytest_addoption(parser: pytest.Parser):
    parser.addoption(
        "--targetpath",
        action="append",
        default=[],
        help="Specify ssh target and fstests path. Can be specified multiple times.\n"
        "eg. devvm:/home/fstests",
    )
    parser.addini(
        "targetpaths",
        type="linelist",
        default=[],
        help="Specify ssh target and fstests path. Can be specified multiple times.\n"
        "eg. devvm:/home/fstests",
    )

    parser.addoption(
        "--mkosi",
        action="store",
        default=None,
        help="Specify number of mkosi hosts to use.",
    )
    parser.addini(
        "mkosi",
        type="string",
        default=None,
        help="Specify number of mkosi hosts to use.",
    )

    parser.addoption(
        "--mkosi-config-dir",
        action="store",
        default=None,
        help="Path to mkosi-config",
    )
    parser.addini(
        "mkosi_config_dir",
        type="string",
        default=None,
        help="Path to mkosi-config",
    )

    parser.addoption(
        "--mkosi-options",
        nargs="+",
        default=[],
        help="Options to pass to mkosi",
    )
    parser.addini(
        "mkosi_options",
        type="linelist",
        default=[],
        help="Options to pass to mkosi",
    )

    parser.addoption(
        "--mkosi-fstests-dir",
        action="store",
        default=None,
        help="Path to fstests source on vm",
    )
    parser.addini(
        "mkosi_fstests_dir",
        type="string",
        default=None,
        help="Path to fstests source on vm",
    )

    parser.addoption(
        "--mkosi-setup-timeout",
        action="store",
        default=None,
        help="How long to wait in seconds for mkosi setup before aborting",
    )
    parser.addini(
        "mkosi_setup_timeout",
        type="string",
        default=60,
        help="How long to wait in seconds for mkosi setup before aborting (default 60s)",
    )

    parser.addoption(
        "--host-fstests-dir",
        action="store",
        default=None,
        help="Path to fstests source on host",
    )
    parser.addini(
        "host_fstests_dir",
        type="string",
        default=None,
        help="Path to fstests source on host",
    )

    parser.addoption(
        "--tests",
        nargs="+",
        default=[],
        help="Individual tests to run (can't be used with group).",
    )
    parser.addini(
        "tests",
        type="linelist",
        default=[],
        help="Individual tests to run (can't be used with group).",
    )

    parser.addoption(
        "--excludes",
        nargs="+",
        default=[],
        help="Individual tests to exclude",
    )
    parser.addini(
        "excludes",
        type="linelist",
        default=[],
        help="Individual tests to exclude",
    )

    parser.addoption(
        "--exclude-file",
        action="store",
        default=None,
        help="Path to an exclude file with a test per line to exclude from test run.",
    )
    parser.addini(
        "exclude_file",
        type="string",
        default=None,
        help="Path to an exclude file with a test per line to exclude from test run.",
    )

    parser.addoption(
        "--group",
        action="store",
        default=None,
        help="Which group to run; equivalent to fstests -g",
    )
    parser.addini(
        "group",
        type="string",
        default=None,
        help="Which group to run; equivalent to fstests -g",
    )

    parser.addoption(
        "--random",
        action="store_true",
        default=False,
        help="Randomize the order of tests.",
    )
    parser.addini(
        "random",
        type="bool",
        default=False,
        help="Randomize the order of tests.",
    )

    parser.addoption(
        "--no-cleanup-on-failure",
        action="store_true",
        default=False,
        help="Do not cleanup machine if a test fails on it.",
    )
    parser.addini(
        "no_cleanup_on_failure",
        type="bool",
        default=False,
        help="Do not cleanup machine if a test fails on it.",
    )

    parser.addoption(
        "--results-path",
        action="store",
        default=None,
        help="Path to results directory",
    )
    parser.addini(
        "results_path",
        type="string",
        default=None,
        help="Path to results directory",
    )


def __num_machines(config):
    num_mkosis = __num_mkosi(config)
    targetpaths = __targetpaths(config)

    if (num_machines := num_mkosis + len(targetpaths)) == 0:
        raise ValueError("no vms specified")

    return num_machines


def __num_mkosi(config):
    if (num := config.getoption("--mkosi")) is not None:
        return int(num)
    if (num := config.getini("mkosi")) is not None:
        return int(num)
    return 0


@pytest.fixture(scope="session")
def num_mkosi(request):
    return __num_mkosi(request.config)


def __targetpaths(config):
    return config.getoption("--targetpath") + config.getini("targetpaths")


@pytest.fixture(scope="session")
def targetpaths(request):
    return __targetpaths(request.config)


def __mkosi_config_dir(config):
    return config.getoption("--mkosi-config-dir") or config.getini("mkosi_config_dir")


@pytest.fixture(scope="session")
def mkosi_config_dir(request):
    return __mkosi_config_dir(request.config)


def __mkosi_options(config):
    return " ".join(
        config.getoption("--mkosi-options") + config.getini("mkosi_options")
    )


@pytest.fixture(scope="session")
def mkosi_options(request):
    return __mkosi_options(request.config)


def __mkosi_fstests_dir(config):
    return config.getoption("--mkosi-fstests-dir") or config.getini("mkosi_fstests_dir")


@pytest.fixture(scope="session")
def mkosi_fstests_dir(request):
    return __mkosi_fstests_dir(request.config)


def __mkosi_setup_timeout(config):
    return int(
        config.getoption("--mkosi-setup-timeout")
        or config.getini("mkosi_setup_timeout")
    )


@pytest.fixture(scope="session")
def mkosi_setup_timeout(request):
    return __mkosi_setup_timeout(request.config)


def __no_cleanup_on_failure(config):
    return config.getoption("--no-cleanup-on-failure") or config.getini("no_cleanup_on_failure")

def __results_path(config):
    return config.getoption("--results-path") or config.getini("results_path")


"""
COLLECT TESTS
"""


def get_tests_for_(group, fstests_dir_host):
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
            if group in line or group == "all":
                try:
                    test = f"{dir}/{line.split()[0]}"
                except IndexError:
                    continue

                if "#" in test:
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
def pytest_generate_tests(metafunc):
    group = metafunc.config.getoption("--group") or metafunc.config.getini("group")
    tests = metafunc.config.getoption("--tests") + metafunc.config.getini("tests")

    if group is None and not tests:
        raise ValueError("no tests specified")

    if group and tests:
        raise ValueError("cannot specify both suite and tests")

    if group:
        fstests_dir_host = metafunc.config.getoption(
            "--host-fstests-dir"
        ) or metafunc.config.getini("host_fstests_dir")

        if not isinstance(fstests_dir_host, str):
            raise ValueError("host-fstests-dir not specified")

        tests = get_tests_for_(group, fstests_dir_host)

    assert isinstance(tests, list)

    exclude_file = metafunc.config.getoption(
        "--exclude-file"
    ) or metafunc.config.getini("exclude_file")

    excluded_tests = metafunc.config.getoption("--excludes") + metafunc.config.getini(
        "excludes"
    )

    if exclude_file and excluded_tests:
        raise ValueError("cannot specify both excludes and exclude file")

    if exclude_file:
        with open(exclude_file, "r") as f:
            for line in f:
                if line == "" or line[0] == "#":
                    continue
                excluded_tests.append(line.rstrip())

    tests = [test for test in tests if test not in excluded_tests]

    if len(tests) == 0:
        raise ValueError("no tests specified")

    should_randomize = metafunc.config.getoption("--random") or metafunc.config.getini(
        "random"
    )

    if should_randomize:
        random.seed(float(os.environ["RANDOM_SEED"]))
        random.shuffle(tests)

    metafunc.parametrize("test", tests)


"""
XDIST WORKAROUND

When using pytest-xdist session scoped fixtures run once per process.
I am leveraging FileLock to ensure that session scoped fixtures are
only run once.
"""


def __is_main_process():
    return "PYTEST_XDIST_WORKER" not in os.environ


def pytest_configure(config):
    if __is_main_process():
        os.environ["RANDOM_SEED"] = str(random.random())
        os.environ["TMPDIR"] = tempfile.mkdtemp()

    worker_id = os.environ.get("PYTEST_XDIST_WORKER")
    if worker_id is not None:
        logging.basicConfig(
            filename=f"logs/tests_{worker_id}.log",
            filemode="w",
            level=config.option.log_file_level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
    else:
        logging.basicConfig(
            filename=f"logs/conftest.log",
            filemode="w",
            level=config.option.log_file_level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config):
    try:
        if config.option.numprocesses is None and __is_main_process():
            config.option.numprocesses = __num_machines(config)
    except AttributeError:
        pass


@pytest.fixture(scope="session")
def root_tmp_dir(tmp_path_factory):
    if __is_main_process():
        return tmp_path_factory.mktemp("fast-fstests")
    return tmp_path_factory.getbasetemp().parent


@pytest.fixture(scope="session")
def perform_once(root_tmp_dir):
    def __perform_once(file_name, perform):
        file_path = root_tmp_dir / file_name
        with FileLock(str(file_path) + ".lock"):
            if file_path.is_file():
                with open(file_path, "rb") as f:
                    return pickle.load(f)

            res = perform()

            with open(file_path, "wb") as f:
                pickle.dump(res, f)

            return res

    return __perform_once


"""
MACHINE
"""


@dataclass
class MkosiMachine:
    machine_id: str
    pid: int
    cleanup: bool


@dataclass
class TargetPathMachine:
    target: str
    path: str


Machine = Union[MkosiMachine, TargetPathMachine]


def setup_mkosi_machine(machine_id, mkosi_config_dir, mkosi_options):
    logger.debug("setting up mkosi machine %s", machine_id)
    proc = subprocess.Popen(
        [
            "mkosi",
            "--machine",
            machine_id,
            *(shlex.split(mkosi_options)),
            "qemu",
        ],
        start_new_session=True,
        cwd=mkosi_config_dir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    return MkosiMachine(machine_id, proc.pid, True)


def cleanup_mkosi_machine(machine: MkosiMachine, mkosi_config_dir):
    if not machine.cleanup:
        logger.debug("not cleaning up machine %s", machine.machine_id)
        return

    logger.debug("sending poweroff %s", machine.machine_id)
    poweroff_status = subprocess.run(
        [
            "mkosi",
            "--machine",
            machine.machine_id,
            "ssh",
            "poweroff",
        ],
        cwd=mkosi_config_dir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).returncode

    logger.debug("poweroff status %d", poweroff_status)
    if poweroff_status == 0 or poweroff_status == 1:
        return

    try:
        logger.debug("sigterm process %s", machine.machine_id)
        os.kill(machine.pid, signal.SIGTERM)
        for _ in range(5):
            try:
                pid, _ = os.waitpid(machine.pid, os.WNOHANG)
                if pid != 0:
                    return
                time.sleep(1)
            except ChildProcessError:
                logger.error(
                    "something went wrong waiting for machine cleanup %s",
                    machine.machine_id,
                )
    except ProcessLookupError:
        logger.debug("process already terminated %s", machine.machine_id)
        return
    except OSError:
        logger.error("something went wrong killing machine %s", machine.machine_id)

    try:
        logger.debug("sigkill process %s", machine.machine_id)
        os.kill(machine.pid, signal.SIGKILL)
    except OSError:
        logger.error("something went wrong killing machine %s", machine.machine_id)


def wait_for_mkosi_machine(
    machine: MkosiMachine,
    mkosi_config_dir,
    mkosi_setup_timeout,
):
    logger.debug("waiting for mkosi machine %s...", machine.machine_id)
    for _ in range(mkosi_setup_timeout):
        try:
            # check if the pid exists
            os.kill(machine.pid, 0)
        except OSError:
            logger.warning("machine %s is not running!", machine.machine_id)
            raise ConnectionError(
                "machine is not running, make sure that your mkosi is built with -f before running fast-fstests"
            )

        logger.debug("poking machine %s", machine.machine_id)
        proc = subprocess.run(
            [
                "mkosi",
                "--machine",
                machine.machine_id,
                "ssh",
                "echo POKE",
            ],
            cwd=mkosi_config_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        logger.debug(
            "machine %s status %d %s %s",
            machine.machine_id,
            proc.returncode,
            proc.stdout.decode(),
            proc.stderr.decode(),
        )

        if proc.returncode == 0:
            return

        time.sleep(1)

    raise ValueError("mkosi setup took too long")


@pytest.fixture
def run_test_(
    request,
    mkosi_config_dir,
    mkosi_fstests_dir,
):
    machine = __get_machine()

    def __run_test_(test):
        if isinstance(machine, MkosiMachine):
            if mkosi_fstests_dir is None:
                raise ValueError("must specify path to fstests for mkosi")

            proc = subprocess.run(
                [
                    "mkosi",
                    "--machine",
                    machine.machine_id,
                    "ssh",
                    f"cd {mkosi_fstests_dir} ; ./check {test}",
                ],
                cwd=mkosi_config_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            record_test(__results_path(request.config), test, proc.returncode, proc.stdout, proc.stderr)
    
            if proc.returncode != 0 and __no_cleanup_on_failure(request.config):
                logger.debug("not cleaning up")
                machine.cleanup = False
                __save_machine(machine)

            return proc.returncode, proc.stdout, proc.stderr

        elif isinstance(machine, TargetPathMachine):
            proc = subprocess.run(
                [
                    "ssh",
                    machine.target,
                    f"cd {machine.path} ; sudo ./check {test}",
                ],
                cwd=mkosi_config_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            record_test(__results_path(request.config), test, proc.returncode, proc.stdout, proc.stderr)
            return proc.returncode, proc.stdout, proc.stderr

    return __run_test_


@pytest.hookimpl
def pytest_xdist_setupnodes(config):
    num_mkosi = __num_mkosi(config)
    targetpaths = __targetpaths(config)
    id = 0

    for _ in range(num_mkosi):
        worker_id = f"gw{id}"
        id += 1

        machine = setup_mkosi_machine(
            worker_id, __mkosi_config_dir(config), __mkosi_options(config)
        )

        with open(os.path.join(__tmpdir(), worker_id), "wb") as f:
            pickle.dump(machine, f)

    for targetpath in targetpaths:
        worker_id = f"gw{id}"
        id += 1

        machine = TargetPathMachine(*targetpath.split(":"))

        with open(os.path.join(__tmpdir(), worker_id), "wb") as f:
            pickle.dump(machine, f)


@pytest.hookimpl
def pytest_configure_node(node):
    worker_id = node.gateway.id
    if worker_id is None:
        return

    with open(os.path.join(__tmpdir(), worker_id), "rb") as f:
        machine: Machine = pickle.load(f)

    if isinstance(machine, MkosiMachine):
        wait_for_mkosi_machine(
            machine,
            __mkosi_config_dir(node.config),
            __mkosi_setup_timeout(node.config),
        )

    elif isinstance(machine, TargetPathMachine):
        pass


@pytest.hookimpl
def pytest_sessionfinish(session):
    if __is_main_process():
        shutil.rmtree(__tmpdir())
    else:
        machine = __get_machine()
        if isinstance(machine, MkosiMachine):
            cleanup_mkosi_machine(machine, __mkosi_config_dir(session.config))


def __tmpdir():
    if (tmpdir := os.environ.get("TMPDIR")) is None:
        raise ValueError("no tmp dir")
    return tmpdir


def __get_machine():
    if (worker_id := os.environ.get("PYTEST_XDIST_WORKER")) is None:
        raise ValueError("no worker_id found")

    with open(os.path.join(__tmpdir(), worker_id), "rb") as f:
        machine = pickle.load(f)
        return machine

def __save_machine(machine):
    if (worker_id := os.environ.get("PYTEST_XDIST_WORKER")) is None:
        raise ValueError("no worker_id found")

    with open(os.path.join(__tmpdir(), worker_id), "wb") as f:
        machine = pickle.dump(machine, f)


"""
RESULTS DB
"""

def record_test(results_dir, test, return_code, stdout, stderr):
    if not results_dir:
        logger.debug("no results dir", test)
        return

    logger.debug("recording test %s", test)
    latest_test_path = os.path.join(results_dir, test, str(int(time.time())))
    os.makedirs(latest_test_path)
    
    with open(os.path.join(latest_test_path, "retcode"), "w") as f:
        f.write(str(return_code))

    with open(os.path.join(latest_test_path, "stdout"), "w") as f:
        f.write(stdout.decode())

    with open(os.path.join(latest_test_path, "stderr"), "w") as f:
        f.write(stderr.decode())

"""
CUSTOM SUMMARIES
"""


def get_failures(stats) -> List[str]:
    failed_tests = []
    test_name_pattern = re.compile(r"::test\[(.+)\]")

    for report in stats.get("failed", []):
        match = test_name_pattern.search(report.nodeid)
        if match is not None:
            failed_tests.append(match.group(1))

    return failed_tests


def pytest_terminal_summary(
    terminalreporter, exitstatus, config: pytest.Config
):
    failures = get_failures(terminalreporter.stats)
    if failures:
        terminalreporter.ensure_newline()
        terminalreporter.write_sep("*", "rerun failures", purple=True)
        terminalreporter.write(f"--tests {' '.join(failures)}\n")
