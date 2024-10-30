import asyncio
import logging
import shlex
from asyncio.subprocess import DEVNULL, PIPE, Process
from contextlib import asynccontextmanager
from dataclasses import dataclass
from signal import SIGINT
from typing import List

import pytest

logger = logging.getLogger(__name__)


@dataclass
class MachinePool:
    ready_machines: List[str]
    lock: asyncio.Lock
    semaphore: asyncio.Semaphore


async def set_machine_ready(
    machine_id, machine_pool: MachinePool, mkosi_kernel_dir
):
    while True:
        await asyncio.sleep(1)
        proc = await asyncio.subprocess.create_subprocess_exec(
            "mkosi",
            "--machine",
            machine_id,
            "ssh",
            "echo POKE",
            cwd=mkosi_kernel_dir,
            stdin=DEVNULL,
            stdout=DEVNULL,
            stderr=DEVNULL,
        )

        status = await proc.wait()
        logger.info("machine %s status %d", machine_id, status)

        if status == 0:
            break
        elif status == 1 or status == 255:
            continue
        else:
            raise ValueError("Machine ready status not recognized", status)

    async with machine_pool.lock:
        machine_pool.ready_machines.append(machine_id)
    machine_pool.semaphore.release()


async def setup_machine(
    machine_id,
    mkosi_kernel_dir,
    mkosi_options,
):
    proc = await asyncio.subprocess.create_subprocess_exec(
        "mkosi",
        "--machine",
        machine_id,
        *(shlex.split(mkosi_options)),
        "qemu",
        cwd=mkosi_kernel_dir,
        stdin=DEVNULL,
        stdout=DEVNULL,
        stderr=DEVNULL,
    )

    return proc


async def cleanup_machine(machine_id, proc: Process, mkosi_kernel_dir):
    logger.info("cleaning up machine %s", machine_id)
    status = await (
        await asyncio.subprocess.create_subprocess_exec(
            "mkosi",
            "--machine",
            machine_id,
            "ssh",
            "poweroff",
            cwd=mkosi_kernel_dir,
            stdin=DEVNULL,
            stdout=DEVNULL,
            stderr=DEVNULL,
        )
    ).wait()

    if status != 0:
        proc.send_signal(SIGINT)
        await asyncio.wait_for(proc.wait(), timeout=10)


@pytest.fixture(scope="session", autouse=True)
async def machine_pool(num_machines, mkosi_kernel_dir, mkosi_options):
    machine_ids = [str(i) for i in range(num_machines)]
    machine_pool = MachinePool([], asyncio.Lock(), asyncio.Semaphore(0))

    procs = await asyncio.gather(
        *(
            setup_machine(machine_id, mkosi_kernel_dir, mkosi_options)
            for machine_id in machine_ids
        )
    )

    tasks = [
        asyncio.create_task(
            set_machine_ready(machine_id, machine_pool, mkosi_kernel_dir)
        )
        for machine_id in machine_ids
    ]

    try:
        yield machine_pool
    finally:
        for task in tasks:
            task.cancel()
            await task

        await asyncio.gather(
            *(
                cleanup_machine(machine_id, proc, mkosi_kernel_dir)
                for machine_id, proc in zip(machine_ids, procs)
            )
        )


@asynccontextmanager
async def get_machine(machine_pool: MachinePool):
    await machine_pool.semaphore.acquire()
    async with machine_pool.lock:
        machine_id = machine_pool.ready_machines.pop()

    try:
        yield machine_id
    finally:
        async with machine_pool.lock:
            machine_id = machine_pool.ready_machines.append(machine_id)
        machine_pool.semaphore.release()


@pytest.mark.asyncio_cooperative
async def test(test, machine_pool, mkosi_kernel_dir, fstests_dir):
    logger.info("running test %s looking for machine...", test)
    async with get_machine(machine_pool) as machine_id:
        logger.info("running test %s found machine %s", test, machine_id)
        proc = await asyncio.subprocess.create_subprocess_exec(
            "mkosi",
            "--machine",
            machine_id,
            "ssh",
            f"cd {fstests_dir} ; ./check {test}",
            cwd=mkosi_kernel_dir,
            stdin=DEVNULL,
            stdout=PIPE,
            stderr=PIPE,
        )

        status = await proc.wait()
        stdout, stderr = await proc.communicate()

        if "[not run]" in stdout.decode():
            pytest.skip()

    if status != 0:
        assert stdout.decode() == ""
