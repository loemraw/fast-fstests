from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path


class TestStatus(Enum):
    PASS = auto()
    FAIL = auto()
    SKIP = auto()
    ERROR = auto()


@dataclass
class TestResult:
    name: str
    status: TestStatus
    duration: float
    timestamp: datetime
    summary: str | None
    retcode: int | None
    stdout: bytes | None
    stderr: bytes | None

    @staticmethod
    def from_error(
        name: str, summary: str, duration: float, timestamp: datetime
    ) -> "TestResult":
        return TestResult(
            name, TestStatus.ERROR, duration, timestamp, summary, None, None, None
        )


@dataclass
class Test(ABC):
    name: str
    test_cmd: str
    artifact_paths: list[Path] = field(default_factory=list)
    _id: str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
    )

    @property
    def id(self) -> str:
        return self._id

    @abstractmethod
    def make_result(
        self,
        duration: float,
        retcode: int,
        stdout: bytes,
        stderr: bytes,
    ) -> TestResult:
        pass
