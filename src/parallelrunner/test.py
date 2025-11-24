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
    status: TestStatus
    name: str
    duration: float
    timestamp: datetime
    summary: str | None
    retcode: int | None
    stdout: bytes | None
    stderr: bytes | None
    artifacts: dict[str, bytes]


@dataclass
class Test(ABC):
    name: str
    test_cmd: str
    artifact_paths: list[Path] = field(default_factory=list)
    result: TestResult | None = None

    @abstractmethod
    def set_result(
        self,
        duration: float,
        retcode: int,
        stdout: bytes,
        stderr: bytes,
        artifacts: dict[str, bytes],
    ):
        pass

    def set_result_error(self, msg: str, duration: float):
        self.result = TestResult(
            status=TestStatus.ERROR,
            name=self.name,
            duration=duration,
            timestamp=datetime.now(),
            summary=msg,
            retcode=None,
            stdout=None,
            stderr=None,
            artifacts={},
        )
