import asyncio
import random
import shutil
import string
import subprocess
import time
from asyncio.subprocess import DEVNULL, PIPE, Process
from collections.abc import Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from types import TracebackType
from typing import IO, AnyStr, Self, override

from fastfstests.config import Config
from parallelrunner.supervisor import Supervisor
from parallelrunner.test import Test


class MkosiSupervisor(Supervisor):
    @staticmethod
    def from_config(config: Config) -> Iterable["MkosiSupervisor"]:
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
        self._exited: bool = False
        self.mkosi_command: list[str] = [
            self.mkosi_path,
            "--machine",
            self.name,
            *self.config.mkosi.options,
            "qemu",
        ]

    def build(self, forces: int):
        build_command = list(self.mkosi_command)
        build_command.insert(-1, f"-{'f' * forces}")
        build_command[-1] = "build"
        proc = subprocess.run(
            build_command,
            cwd=self.config.mkosi.config,
        )
        assert proc.returncode == 0, (
            "build failed",
            proc.returncode,
            build_command,
        )

    @override
    async def __aenter__(self) -> Self:
        proc = await asyncio.create_subprocess_exec(
            *self.mkosi_command,
            cwd=self.config.mkosi.config,
            stdin=DEVNULL,
            stdout=PIPE,
            stderr=PIPE,
        )
        self.proc = proc
        try:
            await asyncio.wait_for(self.wait_for_machine(), self.config.mkosi.timeout)
        except TimeoutError:
            self.__cleanup()
            assert False, (
                "timed out waiting for mkosi machine",
                (await self.proc.stdout.read()).decode() if self.proc.stdout else "",
                (await self.proc.stderr.read()).decode() if self.proc.stderr else "",
            )
        except asyncio.CancelledError:
            self.__cleanup()
        return self

    @override
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ):
        self.__cleanup()

    def __cleanup(self):
        self._exited = True
        if self.proc is None:
            return
        try:
            self.proc.terminate()
        except ProcessLookupError:
            pass

    @override
    async def run_test(self, test: Test, timeout: int | None):
        start = time.time()
        results = await self.run_command(test.test_cmd, timeout)
        end = time.time()
        duration = end - start

        if results is None:
            test.set_result_error("timed out", duration)
            return

        retcode, stdout, stderr = results
        test.set_result(
            end - start, retcode, stdout, stderr, await self.collect_artifacts(test)
        )

    @asynccontextmanager
    @override
    async def trace(
        self,
        command: str | None,
        stdout: int | IO[AnyStr] | None,
        stderr: int | IO[AnyStr] | None,
    ):
        if command is None:
            yield
            return

        proc = await self.start_command(command, stdout=stdout, stderr=stderr)
        await asyncio.sleep(2)
        try:
            yield
        finally:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass

    @override
    def __repr__(self):
        return f"mkosi --machine {self.name}"

    @property
    @override
    def exited(self) -> bool:
        return self._exited

    async def start_command(
        self,
        command: str,
        stdout: int | IO[AnyStr] | None = PIPE,
        stderr: int | IO[AnyStr] | None = PIPE,
    ):
        return await asyncio.create_subprocess_exec(
            self.mkosi_path,
            *("--machine", self.name, "ssh"),
            command,
            cwd=self.config.mkosi.config,
            stdin=DEVNULL,
            stdout=stdout,
            stderr=stderr,
        )

    async def run_command(
        self, command: str, timeout: int | None
    ) -> tuple[int, bytes, bytes] | None:
        proc = await self.start_command(command)
        try:
            async with asyncio.timeout(timeout):
                stdout, stderr = await proc.communicate()
        except TimeoutError:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            return

        assert proc.returncode is not None, f"command retcode is None for {command}"
        return (proc.returncode, stdout, stderr)

    async def wait_for_machine(self):
        while True:
            assert self.proc is not None and self.proc.returncode is None, (
                "waiting for machine that is not running, make sure to build your image before running fast-fstests",
                "it's important that the image was built using the same flags you pass into fast-fstests",
                f"mkosi invocation: {self.mkosi_command}",
                f"mkosi config path: {self.config.mkosi.config}",
                f"mkosi stdout: {(await self.proc.stdout.read()).decode()}"
                if self.proc is not None and self.proc.stdout is not None
                else "no mkosi stdout",
                f"mkosi stderr: {(await self.proc.stderr.read()).decode()}"
                if self.proc is not None and self.proc.stderr is not None
                else "no mkosi stderr",
            )

            results = await self.run_command("echo POKE", 5)
            if results is None:
                continue
            if results[0] == 0:
                return

    async def collect_artifacts(self, test: Test) -> dict[str, bytes]:
        async def collect_artifact(path: Path) -> tuple[str, bytes] | None:
            res = await self.run_command(f"cat {str(path)}", 5)
            if res is None:
                return
            return path.name, res[1]

        async def collect_artifacts(path: Path) -> list[tuple[str, bytes]]:
            res = await self.run_command(f"ls {str(path)}", 5)
            if res is None:
                return []
            return [
                t
                for t in await asyncio.gather(
                    *[collect_artifact(Path(p.decode())) for p in res[1].splitlines()]
                )
                if t is not None
            ]

        return dict(
            [
                i
                for l in await asyncio.gather(
                    *[collect_artifacts(path) for path in test.artifact_paths]
                )
                for i in l
            ]
        )
