from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import TracebackType
from typing import IO, Self

from .test import Test, TestResult


class SupervisorExited(Exception):
    pass


class Supervisor(ABC):
    @abstractmethod
    async def __aenter__(self) -> Self:
        pass

    @abstractmethod
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ):
        pass

    @abstractmethod
    async def run_test(
        self,
        test: Test,
        timeout: int | None,
        stdout: IO[bytes],
        stderr: IO[bytes],
    ) -> TestResult:
        pass

    @asynccontextmanager
    @abstractmethod
    def trace(
        self,
        command: str | None,
        stdout: IO[bytes] | None,
        stderr:  IO[bytes] | None,
    ) -> AsyncGenerator[None, None]:
        pass

    @property
    @abstractmethod
    def exited(self) -> bool:
        pass

    @abstractmethod
    async def probe(self) -> bool:
        pass
