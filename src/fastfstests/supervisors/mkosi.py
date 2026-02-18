import asyncio
import logging
import random
import shutil
import string
import subprocess
import tarfile
import tempfile
import time
from asyncio.subprocess import DEVNULL, PIPE, Process
from collections.abc import Iterable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import IO, Self, override

from fastfstests.config import RunConfig as Config
from parallelrunner.supervisor import Supervisor
from parallelrunner.test import Test, TestResult

logger = logging.getLogger(__name__)


class MkosiSupervisor(Supervisor):
    @staticmethod
    def from_config(config: Config) -> Iterable["MkosiSupervisor"]:
        for i in range(config.mkosi.num):
            suffix = "".join(random.choice(string.ascii_lowercase) for _ in range(8))
            name: str = f"ff-{i}-{suffix}"
            yield MkosiSupervisor(config, name)

    def __init__(self, config: Config, name: str):
        if config.mkosi.config is None:
            raise ValueError("mkosi config path not specified")

        if (mkosi_path := shutil.which("mkosi")) is None:
            raise FileNotFoundError("mkosi not found on PATH")
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
        if self.config.mkosi.include is not None:
            self.mkosi_command.insert(1, f"--include={self.config.mkosi.include}")

    def build(self, forces: int):
        build_command = list(self.mkosi_command)
        build_command.insert(-1, f"-{'f' * forces}")
        build_command[-1] = "build"
        proc = subprocess.run(
            build_command,
            cwd=self.config.mkosi.config,
        )
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, build_command)

    @override
    async def __aenter__(self) -> Self:
        self._exited = False
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
            exc = TimeoutError("timed out waiting for mkosi machine")
            if self.proc and self.proc.stdout:
                exc.add_note(f"stdout: {(await self.proc.stdout.read()).decode()}")
            if self.proc and self.proc.stderr:
                exc.add_note(f"stderr: {(await self.proc.stderr.read()).decode()}")
            raise exc
        except asyncio.CancelledError:
            self.__cleanup()
            raise
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
    async def run_test(
        self,
        test: Test,
        timeout: int | None,
        stdout: IO[bytes],
        stderr: IO[bytes],
    ) -> TestResult:
        start = time.time()
        retcode = await self.run_command(test.test_cmd, timeout, stdout, stderr)
        end = time.time()
        duration = end - start

        if retcode is None:
            return TestResult.from_error(
                test.name, "timed out", duration, datetime.now()
            )

        _ = stdout.seek(0)
        out = stdout.read()
        _ = stderr.seek(0)
        err = stderr.read()

        return test.make_result(
            duration,
            retcode,
            out,
            err,
        )

    @asynccontextmanager
    @override
    async def trace(
        self,
        command: str | None,
        stdout: IO[bytes] | None,
        stderr: IO[bytes] | None,
    ):
        if command is None:
            yield
            return

        proc = await self.start_command(command, stdout, stderr)
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

    @override
    async def probe(self) -> bool:
        if self.proc is None or self.proc.returncode is not None:
            return False
        retcode = await self.run_command("echo POKE", 5)
        return retcode == 0

    async def start_command(
        self,
        command: str,
        stdout: int | IO[bytes] | None = DEVNULL,
        stderr: int | IO[bytes] | None = DEVNULL,
    ):
        logger.debug(f"running command {command}...")
        return await asyncio.create_subprocess_exec(
            self.mkosi_path,
            *("--machine", self.name, "ssh"),
            command,
            cwd=self.config.mkosi.config,
            stdin=DEVNULL,
            stdout=stdout or DEVNULL,
            stderr=stderr or DEVNULL,
        )

    async def run_command(
        self,
        command: str,
        timeout: int | None,
        stdout: int | IO[bytes] | None = DEVNULL,
        stderr: int | IO[bytes] | None = DEVNULL,
    ) -> int | None:
        proc = await self.start_command(command, stdout, stderr)
        try:
            async with asyncio.timeout(timeout):
                returncode = await proc.wait()
        except TimeoutError:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            return

        return returncode

    async def wait_for_machine(self):
        while True:
            if self.proc is None or self.proc.returncode is not None:
                exc = RuntimeError("mkosi machine exited unexpectedly")
                exc.add_note("make sure to build your image before running fast-fstests")
                exc.add_note(
                    "the image must be built using the same flags you pass into fast-fstests"
                )
                exc.add_note(f"mkosi invocation: {self.mkosi_command}")
                exc.add_note(f"mkosi config path: {self.config.mkosi.config}")
                if self.proc is not None and self.proc.stdout is not None:
                    exc.add_note(
                        f"mkosi stdout: {(await self.proc.stdout.read()).decode()}"
                    )
                if self.proc is not None and self.proc.stderr is not None:
                    exc.add_note(
                        f"mkosi stderr: {(await self.proc.stderr.read()).decode()}"
                    )
                raise exc

            with tempfile.TemporaryFile("wb+") as stdout:
                with tempfile.TemporaryFile("wb+") as stderr:
                    retcode = await self.run_command(
                        "echo POKE", 5, stdout=stdout, stderr=stderr
                    )
                    _ = stdout.seek(0)
                    _ = stderr.seek(0)
                    logger.debug(
                        f"waiting for machine {self!r}\nstdout %s\nstderr %s",
                        stdout.read(),
                        stderr.read(),
                    )
            if retcode is None:
                continue
            if retcode == 0:
                return
            await asyncio.sleep(1)

    @override
    async def collect_artifacts(self, test: Test, dest: Path):
        if not test.artifact_paths:
            logger.debug("no artifact paths defined for test %s", test.name)
            return

        paths = " ".join(str(p) for p in test.artifact_paths)
        logger.debug("collecting artifacts for %s, patterns: %s", test.name, paths)

        # globstar must be enabled for ** expansion — use bash -O globstar
        cmd = f"bash -O globstar -c 'tar -cf - {paths} 2>/dev/null'"

        with tempfile.TemporaryFile("wb+") as stderr_f:
            with tempfile.TemporaryFile("wb+") as f:
                retcode = await self.run_command(cmd, 10, stdout=f, stderr=stderr_f)

                if retcode is None:
                    logger.warning(
                        "artifact collection timed out for %s", test.name
                    )
                    return
                if retcode != 0:
                    _ = stderr_f.seek(0)
                    cmd_stderr = stderr_f.read().decode(errors="replace").strip()
                    logger.warning(
                        "artifact collection failed for %s (rc=%d): %s",
                        test.name,
                        retcode,
                        cmd_stderr or "(no stderr)",
                    )

                _ = f.seek(0)
                if f.read(1) == b"":
                    logger.debug("no artifacts found for %s", test.name)
                    return
                _ = f.seek(0)

                collected: list[str] = []
                with tarfile.open(fileobj=f) as tar:
                    for member in tar.getmembers():
                        if member.isfile():
                            data = tar.extractfile(member)
                            if data:
                                name = Path(member.name).name
                                _ = (dest / name).write_bytes(data.read())
                                collected.append(name)

                logger.debug(
                    "collected %d artifacts for %s: %s",
                    len(collected),
                    test.name,
                    ", ".join(collected),
                )
