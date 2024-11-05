import logging
import pickle
import shlex
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import List

import pytest
from filelock import FileLock

pytest_plugins = ("xdist",)

logging.basicConfig()
logger = logging.getLogger(__name__)


@dataclass
class MachinePool:
    machine_ids: List[str]
    available_machines: List[str]
    pkl_path: Path


def setup_machine(machine_id, mkosi_kernel_dir, mkosi_options):
    proc = subprocess.Popen(
        [
            "mkosi",
            "--machine",
            machine_id,
            *(shlex.split(mkosi_options)),
            "qemu",
        ],
        cwd=mkosi_kernel_dir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    return proc


def setup_machine_pool(
    num_machines: int,
    pkl_path: Path,
    mkosi_kernel_dir,
    mkosi_options,
):
    machine_ids = list(map(str, range(num_machines)))
    procs = []
    for machine_id in machine_ids:
        procs.append(
            setup_machine(machine_id, mkosi_kernel_dir, mkosi_options)
        )

    return MachinePool(machine_ids, [], pkl_path), procs


def wait_for_machine_pool(mp: MachinePool, mkosi_kernel_dir):
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
                cwd=mkosi_kernel_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            if proc.returncode == 0:
                mp.available_machines.append(machine_id)


def cleanup_machine(machine_id: str, proc: subprocess.Popen, mkosi_kernel_dir):
    poweroff_proc = subprocess.run(
        [
            "mkosi",
            "--machine",
            machine_id,
            "ssh",
            "poweroff",
        ],
        cwd=mkosi_kernel_dir,
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


def cleanup_machine_pool(mp, procs, mkosi_kernel_dir):
    for machine_id, proc in zip(mp.machine_ids, procs):
        cleanup_machine(machine_id, proc, mkosi_kernel_dir)


@pytest.fixture(scope="module", autouse=True)
def machine_pool(
    num_machines,
    mkosi_kernel_dir,
    mkosi_options,
    tmp_path_factory: pytest.TempPathFactory,
):
    procs = None

    root_tmp_dir = tmp_path_factory.getbasetemp().parent
    fn = root_tmp_dir / "machine_pool.pkl"
    with FileLock(str(fn) + ".lock"):
        if fn.is_file():
            with open(fn, "rb") as f:
                mp = pickle.load(f)
        else:
            mp, procs = setup_machine_pool(
                num_machines,
                fn,
                mkosi_kernel_dir,
                mkosi_options,
            )

            wait_for_machine_pool(mp, mkosi_kernel_dir)

            with open(fn, "wb") as f:
                pickle.dump(mp, f)

    yield mp

    if procs is None:
        return

    cleanup_machine_pool(mp, procs, mkosi_kernel_dir)


@contextmanager
def get_machine(mp: MachinePool):
    machine_id = None
    while machine_id is None:
        with FileLock(str(mp.pkl_path) + ".lock"):
            with open(mp.pkl_path, "rb") as f:
                mp = pickle.load(f)
            if mp.available_machines:
                machine_id = mp.available_machines.pop()
            with open(mp.pkl_path, "wb") as f:
                pickle.dump(mp, f)

    try:
        yield machine_id
    finally:
        with FileLock(str(mp.pkl_path) + ".lock"):
            with open(mp.pkl_path, "rb") as f:
                mp = pickle.load(f)
            mp.available_machines.append(machine_id)
            with open(mp.pkl_path, "wb") as f:
                pickle.dump(mp, f)


def __test(test, mp, mkosi_kernel_dir, fstests_dir):
    with get_machine(mp) as machine_id:
        proc = subprocess.run(
            [
                "mkosi",
                "--machine",
                machine_id,
                "ssh",
                f"cd {fstests_dir} ; ./check {test}",
            ],
            cwd=mkosi_kernel_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    return proc.returncode, proc.stdout, proc.stderr


def test(test, machine_pool, mkosi_kernel_dir, fstests_dir):
    status, stdout, _ = __test(
        test, machine_pool, mkosi_kernel_dir, fstests_dir
    )

    if "[not run]" in stdout.decode():
        pytest.skip()

    if status != 0:
        assert stdout.decode() == ""
