import json
import os
import pickle
import random
import shlex
import subprocess
import sys
import time
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from typing import List

import pytest
from filelock import FileLock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db import Invocation, TestResult

"""
PYTEST OPTIONS
"""


@pytest.hookimpl
def pytest_addoption(parser: pytest.Parser):
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
        help="Path to fstests source on host",
    )
    parser.addini(
        "fstests_dir_host",
        type="string",
        default=None,
        help="Path to fstests source on host",
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
        "--exclude",
        action="append",
        default=None,
        help="List of individual tests to not run",
    )
    parser.addini(
        "exclude",
        type="args",
        default=None,
        help="List of individual tests to not run",
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
        default=None,
        help="Randomize the order of tests.",
    )
    parser.addini(
        "random",
        type="bool",
        default=None,
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


@pytest.fixture(scope="session")
def num_machines():
    if (num_processes := os.getenv("PYTEST_XDIST_WORKER_COUNT")) is None:
        return 1
    return int(num_processes)


@pytest.fixture(scope="session")
def mkosi_config_dir(request):
    dir = request.config.getoption(
        "--mkosi-config-dir"
    ) or request.config.getini("mkosi_config_dir")
    if dir is None:
        raise ValueError("mkosi-config-dir not specified!")
    return dir


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


@pytest.fixture(scope="session")
def results_db_path(request):
    return request.config.getoption(
        "--results-db-path"
    ) or request.config.getini("results_db_path")


"""
COLLECT TESTS
"""


def get_tests_for_(group, excluded_tests, fstests_dir_host):
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
                if test in excluded_tests or test == "#":
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

        excluded_tests = metafunc.config.getoption(
            "--exclude"
        ) or metafunc.config.getini("exclude")

        tests = get_tests_for_(group, excluded_tests, fstests_dir_host)

    assert isinstance(tests, list)

    should_randomize = metafunc.config.getoption(
        "--random"
    ) or metafunc.config.getini("random")

    if should_randomize:
        random.seed(float(os.environ["RANDOM_SEED"]))
        random.shuffle(tests)

    metafunc.parametrize("test", tests)


def pytest_configure(config):
    if "PYTEST_XDIST_WORKER_COUNT" not in os.environ:
        os.environ["RANDOM_SEED"] = str(random.random())


"""
XDIST WORKAROUND
"""


@pytest.fixture(scope="session")
def root_tmp_dir(tmp_path_factory, worker_id):
    # if xdist is "disabled" use a temp dir
    if worker_id == "master":
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
class MachinePool:
    machine_ids: List[str]
    available_machines: List[str]
    finisehd_sessions: int
    pkl_name: str


def setup_machine(machine_id, mkosi_config_dir, mkosi_options):
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


def setup_machine_pool(
    num_machines: int,
    pkl_name: str,
    mkosi_config_dir,
    mkosi_options,
):
    machine_ids = list(map(str, range(num_machines)))
    procs = []
    for machine_id in machine_ids:
        procs.append(
            setup_machine(machine_id, mkosi_config_dir, mkosi_options)
        )

    return MachinePool(machine_ids, [], 0, pkl_name), procs


def wait_for_machine_pool(mp: MachinePool, mkosi_config_dir):
    while len(mp.available_machines) != len(mp.machine_ids):
        for machine_id in mp.machine_ids:
            if machine_id in mp.available_machines:
                continue

            proc = subprocess.run(
                [
                    "mkosi",
                    "--machine",
                    machine_id,
                    "ssh",
                    "echo POKE",
                ],
                cwd=mkosi_config_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            if proc.returncode == 0:
                mp.available_machines.append(machine_id)


def cleanup_machine(machine_id: str, proc: subprocess.Popen, mkosi_config_dir):
    poweroff_proc = subprocess.run(
        [
            "mkosi",
            "--machine",
            machine_id,
            "ssh",
            "poweroff",
        ],
        cwd=mkosi_config_dir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if poweroff_proc.returncode == 0:
        return

    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def cleanup_machine_pool(mp, procs, mkosi_config_dir):
    for machine_id, proc in zip(mp.machine_ids, procs):
        cleanup_machine(machine_id, proc, mkosi_config_dir)


@pytest.fixture(scope="session")
def machine_pool(
    num_machines,
    mkosi_config_dir,
    mkosi_options,
    perform_once,
    lock,
    lockless_load,
    lockless_store,
):
    procs = None
    pkl_name = "machine_pool.pkl"

    def perform():
        nonlocal procs
        mp, procs = setup_machine_pool(
            num_machines,
            pkl_name,
            mkosi_config_dir,
            mkosi_options,
        )

        wait_for_machine_pool(mp, mkosi_config_dir)

        return mp

    mp = perform_once(pkl_name, perform)
    yield mp

    with lock(pkl_name):
        mp = lockless_load(pkl_name)
        mp.finisehd_sessions += 1
        lockless_store(pkl_name, mp)

    if procs is None:
        return

    while True:
        with lock(pkl_name):
            mp = lockless_load(pkl_name)
            if mp.finisehd_sessions == num_machines:
                break

    cleanup_machine_pool(mp, procs, mkosi_config_dir)


@pytest.fixture
def machine_id(machine_pool: MachinePool, lock, lockless_load, lockless_store):
    machine_id = None
    while machine_id is None:
        with lock(machine_pool.pkl_name):
            machine_pool = lockless_load(machine_pool.pkl_name)
            if machine_pool.available_machines:
                machine_id = machine_pool.available_machines.pop()
            lockless_store(machine_pool.pkl_name, machine_pool)

    yield machine_id

    with lock(machine_pool.pkl_name):
        machine_pool = lockless_load(machine_pool.pkl_name)
        assert isinstance(machine_id, str)
        machine_pool.available_machines.append(machine_id)
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
def db_session(db_sessionmaker):
    if db_sessionmaker is None:
        return

    with db_sessionmaker() as session:
        yield session


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
    db_session,
    get_pytest_options,
    get_pytest_invocation,
    mkosi_version,
    mkosi_config,
    perform_once,
):
    if db_session is None:
        return

    def __record_invocation():
        invocation = Invocation(
            python_version=sys.version,
            pytest_version=pytest.__version__,
            pytest_options=get_pytest_options,
            pytest_invocation=get_pytest_invocation,
            mkosi_version=mkosi_version,
            mkosi_config=mkosi_config,
        )
        db_session.add(invocation)
        db_session.commit()
        return invocation.id

    return perform_once("invocation_id.pkl", __record_invocation)


@pytest.fixture(scope="function")
def record_test(db_session, request: pytest.FixtureRequest, invocation_id):
    if db_session is None:
        return

    status = None
    return_code = None
    stdout = None
    stderr = None

    def record(_status, _return_code, _stdout, _stderr):
        nonlocal status, return_code, stdout, stderr
        status, return_code, stdout, stderr = (
            _status,
            _return_code,
            _stdout,
            _stderr,
        )

    start = time.time()
    yield record
    end = time.time()

    if status is None:
        # test was never recorded!
        return

    db_session.add(
        TestResult(
            invocation_id=invocation_id,
            name=request.node.funcargs["test"],
            time=end - start,
            status=status,
            return_code=return_code,
            stdout=stdout,
            stderr=stderr,
        )
    )
    db_session.commit()
