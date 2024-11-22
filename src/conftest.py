import json
import logging
import os
import pickle
import random
import shlex
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import List, Sequence, Union

import pytest
from filelock import FileLock
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from src.db import Invocation, TestResult

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
        "--mkosi-option",
        action="append",
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
        "--test",
        action="append",
        default=[],
        help="Individual test to run (can't be used with group). Can be specified multiple times.",
    )
    parser.addini(
        "tests",
        type="linelist",
        default=[],
        help="Individual tests to run (can't be used with group).",
    )

    parser.addoption(
        "--exclude",
        action="append",
        default=[],
        help="Individual test to exclude. Can be specified multiple times.",
    )
    parser.addini(
        "excludes",
        type="linelist",
        default=[],
        help="List of individual tests to exclude",
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
        "--results-db-path",
        action="store",
        default=None,
        help="Path to results sqlite db",
    )
    parser.addini(
        "results_db_path",
        type="string",
        default=None,
        help="Path to results sqlite db",
    )


def __num_machines(config):
    num_mkosis = __num_mkosi(config)
    targetpaths = __targetpaths(config)

    if num_mkosis + len(targetpaths) == 0:
        raise ValueError("no vms specified")

    num_machines = 0

    if num_mkosis is not None:
        num_machines += num_mkosis
    if targetpaths is not None:
        num_machines += len(targetpaths)

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


@pytest.fixture(scope="session")
def mkosi_config_dir(request):
    return request.config.getoption(
        "--mkosi-config-dir"
    ) or request.config.getini("mkosi_config_dir")


@pytest.fixture(scope="session")
def mkosi_options(request):
    return " ".join(
        request.config.getoption("--mkosi-option")
        + request.config.getini("mkosi_options")
    )


@pytest.fixture(scope="session")
def mkosi_fstests_dir(request):
    return request.config.getoption(
        "--mkosi-fstests-dir"
    ) or request.config.getini("mkosi_fstests_dir")


@pytest.fixture(scope="session")
def results_db_path(request):
    return request.config.getoption(
        "--results-db-path"
    ) or request.config.getini("results_db_path")


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
            if group in line:
                test = f"{dir}/{line.split()[0]}"
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
    group = metafunc.config.getoption("--group") or metafunc.config.getini(
        "group"
    )
    tests = metafunc.config.getoption("--test") + metafunc.config.getini(
        "tests"
    )

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

    excluded_tests = metafunc.config.getoption(
        "--exclude"
    ) + metafunc.config.getini("excludes")
    tests = [test for test in tests if test not in excluded_tests]

    should_randomize = metafunc.config.getoption(
        "--random"
    ) or metafunc.config.getini("random")

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

    worker_id = os.environ.get("PYTEST_XDIST_WORKER")
    if worker_id is not None:
        logging.basicConfig(
            filename=f"logs/tests_{worker_id}.log",
            level=logging.INFO,
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
    # if xdist is "disabled" use a temp dir
    if "PYTEST_XDIST_WORKER" not in os.environ:
        return tmp_path_factory.mktemp("fast-fstests")
    return tmp_path_factory.getbasetemp().parent


@pytest.fixture(scope="session")
def lockless_load(root_tmp_dir):
    def __lockless_load(file_name):
        file_path = root_tmp_dir / file_name
        with open(file_path, "rb") as f:
            return pickle.load(f)

    return __lockless_load


@pytest.fixture(scope="session")
def lockless_store(root_tmp_dir):
    def __lockless_store(file_name, obj):
        file_path = root_tmp_dir / file_name
        with open(file_path, "wb") as f:
            return pickle.dump(obj, f)

    return __lockless_store


@pytest.fixture(scope="session")
def lock(root_tmp_dir):
    @contextmanager
    def __lock(file_name):
        file_path = root_tmp_dir / file_name
        with FileLock(str(file_path) + ".lock"):
            yield

    return __lock


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


@dataclass
class TargetPathMachine:
    target: str
    path: str


Machine = Union[MkosiMachine, TargetPathMachine]


@dataclass
class MachinePool:
    available_machines: List[Machine]
    finished_sessions: int
    pkl_name: str


def setup_mkosi_machine(machine_id, mkosi_config_dir, mkosi_options):
    logger.info("setting up mkosi machine %s", machine_id)
    proc = subprocess.Popen(
        [
            "mkosi",
            "--machine",
            machine_id,
            *(shlex.split(mkosi_options)),
            "qemu",
        ],
        cwd=mkosi_config_dir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    return proc


def cleanup_mkosi_machine(
    machine_id: str, proc: subprocess.Popen, mkosi_config_dir
):
    poweroff_status = subprocess.run(
        [
            "mkosi",
            "--machine",
            machine_id,
            "ssh",
            "poweroff",
        ],
        cwd=mkosi_config_dir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).returncode

    if poweroff_status == 0:
        return

    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def setup_mkosi_machines(
    machines: Sequence[MkosiMachine],
    mkosi_config_dir,
    mkosi_options,
):
    if len(machines) > 0 and mkosi_config_dir is None:
        raise ValueError("must configure mkosi-config-dir when using mkosi")

    procs = []
    for machine in machines:
        procs.append(
            setup_mkosi_machine(
                machine.machine_id, mkosi_config_dir, mkosi_options
            )
        )

    return procs


def wait_for_mkosi_machines(
    machines: Sequence[MkosiMachine],
    machine_pool: MachinePool,
    mkosi_config_dir,
):
    logger.info("waiting for mkosi machines...")
    active_machines = 0
    while active_machines < len(machines):
        time.sleep(2)
        logger.info("active machines %d", active_machines)
        for machine in machines:
            if machine in machine_pool.available_machines:
                continue

            logger.info("poking machine %s", machine.machine_id)
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
            logger.info(
                "machine %s status %s %s",
                machine.machine_id,
                proc.stdout.decode(),
                proc.stderr.decode(),
            )

            if proc.returncode == 0:
                logger.info("machine %s is ready", machine.machine_id)
                machine_pool.available_machines.append(machine)
                active_machines += 1


def cleanup_mkosi_machines(
    machines: Sequence[MkosiMachine],
    procs: Sequence[subprocess.Popen],
    mkosi_config_dir,
):
    logger.info("cleaning up machines...")
    for machine, proc in zip(machines, procs):
        logger.info(
            "cleaning up mkosi machine %s w/ output %s %s",
            machine.machine_id,
            proc.stdout.decode(),
            proc.stderr.decode(),
        )
        cleanup_mkosi_machine(machine.machine_id, proc, mkosi_config_dir)


@pytest.fixture(scope="session")
def machine_pool(
    num_mkosi,
    targetpaths,
    mkosi_config_dir,
    mkosi_options,
    perform_once,
    lock,
    lockless_load,
    lockless_store,
):
    procs = None
    mkosi_machines = None
    pkl_name = "machine_pool.pkl"

    def __setup_machine_pool():
        nonlocal procs, mkosi_machines

        mkosi_machines = [MkosiMachine(str(i)) for i in range(num_mkosi)]
        procs = setup_mkosi_machines(
            mkosi_machines,
            mkosi_config_dir,
            mkosi_options,
        )

        machine_pool = MachinePool(
            [TargetPathMachine(*t.split(":")) for t in targetpaths],
            0,
            pkl_name,
        )

        wait_for_mkosi_machines(mkosi_machines, machine_pool, mkosi_config_dir)
        return machine_pool

    machine_pool = perform_once(pkl_name, __setup_machine_pool)
    yield machine_pool

    with lock(pkl_name):
        machine_pool = lockless_load(pkl_name)
        machine_pool.finished_sessions += 1
        lockless_store(pkl_name, machine_pool)

    if procs is None or mkosi_machines is None:
        return

    while True:
        with lock(pkl_name):
            machine_pool = lockless_load(pkl_name)
            if machine_pool.finished_sessions == num_mkosi + len(targetpaths):
                break

    cleanup_mkosi_machines(mkosi_machines, procs, mkosi_config_dir)


@pytest.fixture
def run_test_(
    machine_pool: MachinePool,
    lock,
    lockless_load,
    lockless_store,
    mkosi_config_dir,
    mkosi_fstests_dir,
):
    machine = None
    while machine is None:
        with lock(machine_pool.pkl_name):
            machine_pool = lockless_load(machine_pool.pkl_name)
            assert isinstance(machine_pool, MachinePool)
            if machine_pool.available_machines:
                machine = machine_pool.available_machines.pop()
            lockless_store(machine_pool.pkl_name, machine_pool)

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

            return proc.returncode, proc.stdout, proc.stderr

    yield __run_test_

    with lock(machine_pool.pkl_name):
        machine_pool = lockless_load(machine_pool.pkl_name)
        machine_pool.available_machines.append(machine)
        lockless_store(machine_pool.pkl_name, machine_pool)


"""
RESULTS DB
"""


@pytest.fixture(scope="session")
def db_sessionmaker(results_db_path):
    if results_db_path is None:
        return
    engine = create_engine(f"sqlite:///{results_db_path}")
    return sessionmaker(bind=engine)


@pytest.fixture(scope="session")
def get_pytest_options(pytestconfig, perform_once):
    def __pytest_options():
        return json.dumps(pytestconfig.inicfg)

    return perform_once("pytest_options.pkl", __pytest_options)


@pytest.fixture(scope="session")
def get_pytest_invocation(pytestconfig, perform_once):
    def __pytest_invocation():
        return json.dumps(pytestconfig.invocation_params.args)

    return perform_once("pytest_invocation.pkl", __pytest_invocation)


@pytest.fixture(scope="session")
def mkosi_version(perform_once):
    def __mkosi_version():
        return subprocess.run(
            ["mkosi", "--version"], capture_output=True
        ).stdout.decode()

    return perform_once("mkosi_version.pkl", __mkosi_version)


@pytest.fixture(scope="session")
def mkosi_config(mkosi_config_dir, mkosi_options, perform_once):
    def __mkosi_config():
        env = os.environ.copy()
        env["PAGER"] = "cat"
        return subprocess.run(
            ["mkosi", *(shlex.split(mkosi_options)), "cat-config"],
            cwd=mkosi_config_dir,
            capture_output=True,
            env=env,
        ).stdout.decode()

    return perform_once("mkosi_config.pkl", __mkosi_config)


@pytest.fixture(scope="session", autouse=True)
def invocation_id(
    db_sessionmaker,
    get_pytest_options,
    get_pytest_invocation,
    mkosi_version,
    mkosi_config,
    perform_once,
):
    if db_sessionmaker is None:
        return

    def __record_invocation():
        invocation = Invocation(
            timestamp=int(time.time()),
            python_version=sys.version,
            pytest_version=pytest.__version__,
            pytest_options=get_pytest_options,
            pytest_invocation=get_pytest_invocation,
            mkosi_version=mkosi_version,
            mkosi_config=mkosi_config,
        )

        try:
            with db_sessionmaker() as session:
                session.add(invocation)
                session.commit()
                return invocation.id
        except OperationalError:
            logger.info("failed to record invocation %s", str(invocation))

    return perform_once("invocation_id.pkl", __record_invocation)


@pytest.fixture(scope="function")
def record_test(
    db_sessionmaker, request: pytest.FixtureRequest, invocation_id
):
    if db_sessionmaker is None:
        return

    status = None
    return_code = None
    summary = None
    stdout = None
    stderr = None

    def record(_status, _return_code, _summary, _stdout, _stderr):
        nonlocal status, return_code, summary, stdout, stderr
        status, return_code, summary, stdout, stderr = (
            _status,
            _return_code,
            _summary,
            _stdout,
            _stderr,
        )

    start = time.time()
    yield record
    end = time.time()

    if status is None:
        # test was never recorded!
        return

    test_result = TestResult(
        invocation_id=invocation_id,
        timestamp=int(time.time()),
        name=request.node.funcargs["test"],
        time=end - start,
        status=status,
        return_code=return_code,
        summary=summary,
        stdout=stdout,
        stderr=stderr,
    )

    try:
        with db_sessionmaker() as session:
            session.add(test_result)
            session.commit()
    except OperationalError:
        logger.info("failed to record test %s", str(test_result))
