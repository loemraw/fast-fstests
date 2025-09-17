from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from pathlib import Path
from types import TracebackType
from typing import Self

from .test import Test, TestResult

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
    def run_tests(self) -> AsyncGenerator[None, Test]:
        pass

    @abstractmethod
    async def collect_artifact(self, path: Path) -> bytes | None:
        pass
