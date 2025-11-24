from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import TracebackType
from typing import IO, AnyStr, Self

from .test import Test


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
    async def run_test(self, test: Test, timeout: int | None):
        pass

    @asynccontextmanager
    @abstractmethod
    def trace(
        self,
        command: str | None,
        stdout: int | IO[AnyStr] | None,
        stderr: int | IO[AnyStr] | None,
    ) -> AsyncGenerator[None, None]:
        pass

    @property
    @abstractmethod
    def exited(self) -> bool:
        pass
