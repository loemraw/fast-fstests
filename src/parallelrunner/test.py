from abc import ABC, abstractmethod
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Callable


class TestStatus(Enum):
    PASS = auto()
    FAIL = auto()
    SKIP = auto()
    ERROR = auto()


@dataclass
class TestResult:
    status: TestStatus
    name: str
    duration: float
    timestamp: datetime
    summary: str
    retcode: int
    stdout: bytes
    stderr: bytes
    artifacts: dict[str, bytes]


@dataclass
class Test(ABC):
    name: str
    test: str
    result: TestResult | None = None

    @abstractmethod
    async def set_result(
        self,
        duration: float,
        retcode: int,
        stdout: bytes,
        stderr: bytes,
        collect_artifact: Callable[[Path], Awaitable[bytes | None]],
    ):
        pass
