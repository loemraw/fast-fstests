import asyncio
import random
import shutil
import string
import time
from asyncio.subprocess import DEVNULL, PIPE, Process
from collections.abc import AsyncGenerator, Iterable
from pathlib import Path
from types import TracebackType
from typing import Self, override

from fastfstests.config import Config
from parallelrunner.supervisor import Supervisor
from parallelrunner.test import Test


class MkosiSupervisor(Supervisor):
    @staticmethod
    def from_config(config: Config) -> Iterable["Supervisor"]:
        for i in range(config.mkosi.num):
            suffix = "".join(random.choice(string.ascii_lowercase) for _ in range(8))
            name: str = f"ff-{i}-{suffix}"
            yield MkosiSupervisor(config, name)

    def __init__(self, config: Config, name: str):
        assert config.mkosi.config is not None, "mkosi config path not specified"

        assert (mkosi_path := shutil.which("mkosi")) is not None, (
            "mkosi not found on path"
        )
        self.mkosi_path: str = mkosi_path

        self.config: Config = config
        self.name: str = name

        self.proc: Process | None = None

    @override
    async def __aenter__(self) -> Self:
        proc = await asyncio.create_subprocess_exec(
            self.mkosi_path,
            *("--machine", self.name, *self.config.mkosi.options, "qemu"),
            cwd=self.config.mkosi.config,
            stdin=DEVNULL,
            stdout=PIPE,
            stderr=PIPE,
        )
        self.proc = proc
        try:
            await asyncio.wait_for(self.wait_for_machine(), self.config.mkosi.timeout)
        except TimeoutError:
            assert False, "timed out waiting for mkosi machine"
        return self

    @override
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ):
        if self.proc is None:
            return
        try:
            self.proc.terminate()
            _ = await asyncio.wait_for(self.proc.wait(), 2)
        except TimeoutError:
            pass
        except ProcessLookupError:
            return

        try:
            self.proc.kill()
            _ = await asyncio.wait_for(self.proc.wait(), 3)
        except TimeoutError:
            pass
        except ProcessLookupError:
            return

    @override
    async def run_tests(self) -> AsyncGenerator[None, Test]:
        test = yield
        while True:
            start = time.time()
            proc = await asyncio.create_subprocess_exec(
                self.mkosi_path,
                *("--machine", self.name, "ssh", test.test),
                cwd=self.config.mkosi.config,
                stdin=DEVNULL,
                stdout=PIPE,
                stderr=PIPE,
            )
            stdout, stderr = await proc.communicate()
            end = time.time()
            retcode = proc.returncode
            assert retcode is not None, "no returncode when running mkosi test"
            await test.set_result(
                end - start, retcode, stdout, stderr, self.collect_artifact
            )
            test = yield

    @override
    async def collect_artifact(self, path: Path) -> bytes | None:
        proc = await asyncio.create_subprocess_exec(
            self.mkosi_path,
            *(
                "--machine",
                self.name,
                "ssh",
                f"cat {path}",
            ),
            cwd=self.config.mkosi.config,
            stdin=DEVNULL,
            stdout=PIPE,
            stderr=PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), 5)
        except TimeoutError:
            return
        if stderr:
            return
        return stdout

    @override
    def __repr__(self):
        return f"mkosi --machine {self.name}"

    async def wait_for_machine(self):
        while True:
            assert self.proc is not None and self.proc.returncode is None, (
                f"waiting for machine that is not running:\n{self.proc.stdout}\n{self.proc.stderr}"
            )

            proc = await asyncio.create_subprocess_exec(
                self.mkosi_path,
                *(
                    "--machine",
                    self.name,
                    "ssh",
                    "echo POKE",
                ),
                cwd=self.config.mkosi.config,
                stdin=DEVNULL,
                stdout=DEVNULL,
                stderr=DEVNULL,
            )

            if await proc.wait() == 0:
                return
            await asyncio.sleep(1)
