import asyncio
import dataclasses
import enum
import json
import signal
import time
from asyncio.subprocess import Process
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from typing import Awaitable, Callable, Optional

from aiofiles import open

FSTESTS_DIR = "/work/build/fstests"
MKOSI_KERNEL_DIR = "/home/leomar/local/mkosi-kernel"
NUM_MACHINES = 10
CHECK_ARGS = "-g auto -s btrfs_normal"
# CHECK_ARGS = "btrfs/304 btrfs/307 btrfs/308 btrfs/309 btrfs/318"


class MachineStatus(enum.Enum):
    DOWN = 1
    SETTING_UP = 2
    UP = 3


@dataclass
class TestPassed:
    test: str
    stdout: str
    stderr: str


@dataclass
class TestFailed:
    test: str
    status: Optional[int]
    stdout: str
    stderr: str


@dataclass
class TestNotRun:
    test: str
    stdout: str
    stderr: str


async def execute(*cmd, **kwargs):
    return await asyncio.create_subprocess_shell(
        " ".join(map(str, cmd)),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **kwargs,
    )


async def get_output(proc: Process):
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout, stderr


async def get_machine_status(machine_id):
    status, _, _ = await get_output(
        await execute(
            "mkosi --machine",
            machine_id,
            "ssh",
            "echo POKE > /dev/null",
            cwd=MKOSI_KERNEL_DIR,
        )
    )

    if status == 1:
        return MachineStatus.DOWN
    elif status == 255:
        return MachineStatus.SETTING_UP
    elif status == 0:
        return MachineStatus.UP

    raise ValueError("Machine status not recognized", machine_id, status)


@asynccontextmanager
async def create_machine(machine_id):
    status = await get_machine_status(machine_id)
    if status != MachineStatus.DOWN:
        raise ValueError("Machine already exists.", machine_id)

    print(f"creating machine {machine_id}...")
    proc = await execute(
        "mkosi --machine",
        machine_id,
        "qemu",
        cwd=MKOSI_KERNEL_DIR,
    )

    print(f"waiting for machine {machine_id}...")

    try:
        await wait_for_machine_status(machine_id)
        await adjust_fstests_config(machine_id)
        yield
    finally:
        print(f"cleaning up machine {machine_id}...")
        proc = await execute(
            "mkosi --machine",
            machine_id,
            "ssh poweroff",
            cwd=MKOSI_KERNEL_DIR,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            print(f"need to kill machine {machine_id}...")
            proc.send_signal(signal.SIGINT)
            await proc.wait()

        print(f"done cleaning up machine {machine_id}")


async def wait_for_machine_status(machine_id):
    while True:
        status = await get_machine_status(machine_id)
        print(f"waiting on machine {machine_id}... current status {status}")
        if status == MachineStatus.UP:
            break
        await asyncio.sleep(3)


async def adjust_fstests_config(machine_id):
    print("adjusting fstests config")

    status, stdout, stderr = await get_output(
        await execute(
            "mkosi --machine",
            machine_id,
            f'ssh "mkfs.btrfs -f /dev/nvme{machine_id}n1"',
            cwd=MKOSI_KERNEL_DIR,
        )
    )

    print(f"status {status}\nstdout\n{stdout}\nstderr\n{stderr}")


async def determine_tests_to_run(
    machine_id,
    check_args,
):
    print("determining tests to run")
    status, stdout, stderr = await get_output(
        await execute(
            "mkosi --machine",
            machine_id,
            'ssh "cd',
            FSTESTS_DIR,
            f"; env TEST_DEV=/dev/nvme{machine_id}n1 ./check -n",
            check_args,
            " | grep -E '[a-zA-Z]+/[0-9]+$'\"",
            cwd=MKOSI_KERNEL_DIR,
        )
    )

    if status != 0:
        raise ValueError("Unable to determine tests", status, stdout, stderr)

    tests = [b.decode() for b in stdout.splitlines()]
    print(f"running tests {tests}")
    return tests


async def run_test(
    machine_id,
    test,
    result_queue: asyncio.Queue,
):
    print(f"running test {test} on machine {machine_id}...")

    start = time.time()

    status, stdout, stderr = await get_output(
        await execute(
            "mkosi --machine",
            machine_id,
            "ssh",
            '"cd',
            FSTESTS_DIR,
            ";",
            f"env TEST_DEV=/dev/nvme{machine_id}n1 ./check",
            test,
            '"',
            cwd=MKOSI_KERNEL_DIR,
        )
    )

    end = time.time()

    print(
        f"finished test {test} on machine {machine_id} in "
        f"{end-start}s with status {status}"
    )

    stdout = stdout.decode()
    stderr = stderr.decode()

    if status == 0:
        if "[not run]" in stdout:
            await result_queue.put(TestNotRun(test, stdout, stderr))
        else:
            await result_queue.put(TestPassed(test, stdout, stderr))
    else:
        await result_queue.put(TestFailed(test, status, stdout, stderr))


async def populate_tests(
    machine_id,
    test_queue: asyncio.Queue,
):
    print(f"head machine {machine_id}")
    print(f"head machine determining tests for {CHECK_ARGS}...")
    tests = await determine_tests_to_run(machine_id, CHECK_ARGS)

    print("head machine filling tests...")
    for test in tests:
        test_queue.put_nowait(test)


async def handle_results(
    result_queue: asyncio.Queue,
):
    not_run = []
    failures = []
    try:
        while result := await result_queue.get():
            if isinstance(result, TestNotRun):
                not_run.append(result)
            elif isinstance(result, TestFailed):
                failures.append(result)

            result_j = dataclasses.asdict(result)
            result_j["timestamp"] = time.time()

            async with open(f"out/{result.test.replace('/', '_')}", "a+") as f:
                await f.write(json.dumps(result_j))
    except asyncio.CancelledError:
        pass
    finally:
        print(f"didn't run {len(not_run)} tests: {[t.test for t in not_run]}")
        print(f"failed {len(failures)} tests: {[t.test for t in failures]}")


async def machine_driver(
    machine_id,
    machine_queue: asyncio.Queue[Callable[[str], Awaitable]],
):
    try:
        async with create_machine(machine_id):
            while task := await machine_queue.get():
                await task(machine_id)
                machine_queue.task_done()
    except asyncio.CancelledError:
        pass


async def main():
    try:
        test_queue = asyncio.Queue()
        result_queue = asyncio.Queue()
        machine_queue = asyncio.Queue()

        tasks = [
            *[
                asyncio.create_task(machine_driver(m, machine_queue))
                for m in range(NUM_MACHINES)
            ],
            asyncio.create_task(handle_results(result_queue)),
        ]

        machine_queue.put_nowait(
            partial(populate_tests, test_queue=test_queue)
        )

        start = time.time()

        await asyncio.wait_for(machine_queue.join(), timeout=60)

        while not test_queue.empty():
            test = await test_queue.get()
            machine_queue.put_nowait(
                partial(run_test, test=test, result_queue=result_queue)
            )
        await machine_queue.join()

        end = time.time()
        print(f"final run time {end-start}s")

        parent = None
        try:
            parent = await asyncio.gather(*tasks)
        except BaseException:
            if parent:
                parent.cancel()

    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    asyncio.run(main())
