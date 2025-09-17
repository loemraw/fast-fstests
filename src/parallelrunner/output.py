import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.rule import Rule
from rich.status import Status
from rich.table import Table

from .supervisor import Supervisor
from .test import Test, TestResult, TestStatus

logger = logging.getLogger(__name__)


@dataclass
class _Summary:
    passed: list[TestResult] = field(default_factory=list)
    failed: list[TestResult] = field(default_factory=list)
    skipped: list[TestResult] = field(default_factory=list)
    errored: list[TestResult] = field(default_factory=list)


class Output:
    def __init__(self, results_dir: Path | None):
        self.console: Console = Console(highlight=False)
        self.results_dir: Path | None = results_dir

        self._summary: _Summary = _Summary()

        self._status: Status | None = None
        self._cnt: int = 0
        self._total: int = 0

        self._supervisors_progress: Progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(complete_style="green", finished_style="green"),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("({task.completed}/{task.total})"),
            TimeElapsedColumn(),
            console=self.console,
        )
        self._supervisor_task_id: TaskID = self._supervisors_progress.add_task(
            "Spawning supervisors..."
        )
        self._supervisor_progress: Progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            TimeElapsedColumn(),
            console=self.console,
        )
        group = Group(self._supervisor_progress, self._supervisors_progress)
        self._supervisor_live: Live = Live(group, console=self.console)

        self._overall_test_progress: Progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]Running tests..."),
            BarColumn(complete_style="green", finished_style="green"),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("({task.completed}/{task.total})"),
            TimeElapsedColumn(),
            console=self.console,
        )
        self._overall_test_task_id: TaskID = self._overall_test_progress.add_task(
            "overall", start=False
        )
        self._test_progress: Progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            TimeElapsedColumn(),
            console=self.console,
        )

        group = Group(self._test_progress, self._overall_test_progress)
        self._live: Live = Live(group, console=self.console)

    @contextmanager
    def spawning_supervisors(self, num_supervisors: int):
        self.console.print()
        self.console.print(Rule(title=" Setting up", align="left"))
        self._supervisors_progress.reset(
            self._supervisor_task_id, total=num_supervisors
        )
        with self._supervisor_live:
            yield self

    @contextmanager
    def spawning_supervisor(self, supervisor: Supervisor):
        task_id = self._supervisor_progress.add_task(repr(supervisor))
        try:
            yield
        finally:
            self._supervisor_progress.remove_task(task_id)
            self._supervisors_progress.advance(self._supervisor_task_id)
            self.console.print(f"  [bold green]spawn[/bold green] {supervisor}")

    @contextmanager
    def cleaning_supervisors(self, num_supervisors: int):
        self.console.print()
        self.console.print(Rule(title=" Cleaning up", align="left"))
        self._supervisors_progress.reset(
            self._supervisor_task_id,
            description="Exiting supervisors...",
            total=num_supervisors,
        )
        with self._supervisor_live:
            yield self

    @contextmanager
    def cleaning_supervisor(self, supervisor: Supervisor):
        task_id = self._supervisor_progress.add_task(repr(supervisor))
        try:
            yield
        finally:
            self._supervisor_progress.remove_task(task_id)
            self._supervisors_progress.advance(self._supervisor_task_id)
            self.console.print(f"  [bold green]exit[/bold green] {supervisor}")

    @contextmanager
    def running_tests(self, num_tests: int):
        self.console.print()
        self.console.print(Rule(title=" Testing", align="left"))
        self._overall_test_progress.reset(self._overall_test_task_id, total=num_tests)
        try:
            with self._live:
                yield self
        finally:
            self.show_test_summary()

    @contextmanager
    def running_test(self, test: Test):
        task_id = self._test_progress.add_task(test.name)
        try:
            yield
        finally:
            self._test_progress.remove_task(task_id)
            assert test.result is not None, "unable to get test result"
            self._test_done(test.result)

    def _test_done(self, result: TestResult):
        self.log_result(result)
        self._overall_test_progress.advance(self._overall_test_task_id)

        match result.status:
            case TestStatus.PASS:
                self._summary.passed.append(result)
                self._test_passed(result)
            case TestStatus.FAIL:
                self._summary.failed.append(result)
                self._test_failed(result)
            case TestStatus.SKIP:
                self._summary.skipped.append(result)
                self._test_skipped(result)
            case TestStatus.ERROR:
                self._summary.errored.append(result)
                self._test_errored(result)

    def log_result(self, result: TestResult):
        if self.results_dir is None:
            return

        date = result.timestamp.strftime("%Y-%m-%d_%H-%M-%S_%f")
        path = self.results_dir.joinpath(result.name, date)
        os.makedirs(path)

        with open(path.joinpath("stdout"), "wb") as f:
            _ = f.write(result.stdout)

        with open(path.joinpath("stderr"), "wb") as f:
            _ = f.write(result.stderr)

        with open(path.joinpath("retcode"), "w") as f:
            _ = f.write(str(result.retcode))

        with open(path.joinpath("duration"), "w") as f:
            _ = f.write(str(result.duration))

        with open(path.joinpath("status"), "w") as f:
            _ = f.write(str(result.status.name))

        for name, value in result.artifacts.items():
            with open(path.joinpath(name), "wb") as f:
                _ = f.write(value)

    def _test_passed(self, result: TestResult):
        self.console.print(
            " ",
            "[bold green]pass[/bold green]",
            result.name,
            f"[yellow]{str(timedelta(seconds=int(result.duration)))}",
        )

    def _test_failed(self, result: TestResult):
        self.console.print(
            " ",
            "[bold red]fail[/bold red]",
            result.name,
            f"[yellow]{str(timedelta(seconds=int(result.duration)))}",
        )

    def _test_skipped(self, result: TestResult):
        self.console.print(
            " ",
            "[bold yellow]skip[/bold yellow]",
            result.name,
            f"[yellow]{str(timedelta(seconds=int(result.duration)))}",
            f"[dim]{result.summary}",
        )

    def _test_errored(self, result: TestResult):
        self.console.print(
            " ",
            "[bold medium_purple3]error[/bold medium_purple3]",
            result.name,
            f"[yellow]{str(timedelta(seconds=int(result.duration)))}",
        )

    def show_test_summary(self):
        for result in self._summary.failed + self._summary.errored:
            if result.status == TestStatus.FAIL:
                status = "[red bold]Failed:[/red bold]"
            else:
                status = "[medium_purple3 bold]Error:[/medium_purple3 bold]"

            self.console.print()
            self.console.print(
                Rule(
                    f" {status} {result.name}",
                    align="left",
                    style="red",
                ),
            )

            if self.results_dir is not None:
                self.console.print(
                    Panel(
                        f"{self.results_dir.joinpath(result.name, result.timestamp.strftime('%Y-%m-%d_%H-%M-%S_%f'))}",
                        title="full results",
                        title_align="left",
                    )
                )
            self.console.print(
                Panel(str(result.retcode), title="retcode", title_align="left")
            )
            self.console.print(
                Panel(result.stdout.decode(), title="stdout", title_align="left")
            )
            self.console.print(
                Panel(result.stderr.decode(), title="stderr", title_align="left")
            )

        table = Table()
        table.add_column("Status")
        table.add_column("Count")

        if self._summary.passed:
            table.add_row(
                "[bold green]Passed[/bold green]", str(len(self._summary.passed))
            )
        if self._summary.skipped:
            table.add_row(
                "[bold yellow]Skipped[/bold yellow]", str(len(self._summary.skipped))
            )
        if self._summary.failed:
            table.add_row("[bold red]Failed[/bold red]", str(len(self._summary.failed)))
        if self._summary.errored:
            table.add_row(
                "[bold medium_purple3]Errored[/bold medium_purple3]",
                str(len(self._summary.errored)),
            )

        self.console.print()
        self.console.print(Rule(" Summary", align="left"))
        self.console.print(table)

    @contextmanager
    def keeping_alive(self):
        self.console.print()
        self.console.rule(" Debug", align="left")
        progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            TimeElapsedColumn(),
            console=self.console,
        )
        task_id = progress.add_task("Keeping alive... (ctrl-C to end)")
        try:
            with progress:
                yield
        finally:
            progress.remove_task(task_id)
