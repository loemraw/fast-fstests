import logging
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.padding import Padding
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

from parallelrunner.supervisor import Supervisor
from parallelrunner.test import Test, TestResult, TestStatus

from .rich_plotext import RichPlotext

logger = logging.getLogger(__name__)


class Output:
    def __init__(
        self,
        results_dir: Path | None,
        print_failure_list: bool = False,
        print_n_slowest: int = 0,
        print_duration_hist: bool = False,
    ):
        self.console: Console = Console(highlight=False)
        self.results_dir: Path | None = results_dir

        self._print_failure_list: bool = print_failure_list
        self._print_n_slowest: int = print_n_slowest
        self._print_duration_hist: bool = print_duration_hist

        self._results: list[TestResult] = []

        self._status: Status | None = None
        self._cnt: int = 0
        self._total: int = 0
        self._duration: int = 0

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
            if supervisor.exited:
                self.console.print(f"  [bold green]exit[/bold green] {supervisor}")
            else:
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

        start_time = datetime.now()
        try:
            with self._live:
                yield self
        finally:
            end_time = datetime.now()
            self._duration = (end_time - start_time).seconds

    @contextmanager
    def running_test(self, test: Test):
        task_id = self._test_progress.add_task(test.name)
        try:
            yield
        finally:
            self._test_progress.remove_task(task_id)
            if test.result is not None:
                self._test_done(test.result)

    def _test_done(self, result: TestResult):
        match result.status:
            case TestStatus.PASS:
                self._test_passed(result)
            case TestStatus.FAIL:
                self._test_failed(result)
            case TestStatus.SKIP:
                self._test_skipped(result)
            case TestStatus.ERROR:
                self._test_errored(result)

        self._overall_test_progress.advance(self._overall_test_task_id)
        self.log_result(result)
        self._results.append(result)

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
            "  [bold green]pass[/bold green]",
            result.name,
            f"[yellow]{str(timedelta(seconds=int(result.duration)))}",
        )

    def _test_failed(self, result: TestResult):
        self.console.print(
            "  [bold red]fail[/bold red]",
            result.name,
            f"[yellow]{str(timedelta(seconds=int(result.duration)))}",
        )

    def _test_skipped(self, result: TestResult):
        self.console.print(
            "  [bold yellow]skip[/bold yellow]",
            result.name,
            f"[yellow]{str(timedelta(seconds=int(result.duration)))}",
            f"[dim]{result.summary}",
        )

    def _test_errored(self, result: TestResult):
        self.console.print(
            "  [bold medium_purple3]error[/bold medium_purple3]",
            result.name,
            f"[yellow]{str(timedelta(seconds=int(result.duration)))}",
            f"[dim]{result.summary}",
        )

    def _print_failed_details(self):
        for result in self._results:
            header: list[str] = []

            if result.status == TestStatus.FAIL:
                header.append("Failed")
            elif result.status == TestStatus.ERROR:
                header.append("Error")
            else:
                continue

            header.append(result.name)

            if self.results_dir is not None:
                header.append("@")
                header.append(
                    str(
                        self.results_dir.joinpath(
                            result.name,
                            result.timestamp.strftime("%Y-%m-%d_%H-%M-%S_%f"),
                        )
                    )
                )

            self.console.print()
            self.console.print(
                Rule(
                    f" {' '.join(header)}",
                    align="left",
                    style="red",
                ),
            )

            if result.stdout:
                self.console.print(
                    Panel.fit(
                        result.stdout.decode(), title="stdout", title_align="left"
                    )
                )
            if result.stderr:
                self.console.print(
                    Panel.fit(
                        result.stderr.decode(), title="stderr", title_align="left"
                    )
                )

    def _print_failed_list(self):
        if results := [
            r for r in self._results if r.status in (TestStatus.FAIL, TestStatus.ERROR)
        ]:
            self.console.print()
            self.console.print(Rule(" Failure List", align="left"))
            self.console.print(
                *set(result.name for result in results),
                soft_wrap=True,
            )

    def _print_result_counts(self):
        self.console.print()
        self.console.print(
            Rule(
                f" Summary",
                align="left",
            ),
        )

        if passed := [r for r in self._results if r.status == TestStatus.PASS]:
            self.console.print(f"  [bold green]Passed[/bold green] {len(passed)}")
        if skipped := [r for r in self._results if r.status == TestStatus.SKIP]:
            self.console.print(f"  [bold yellow]Skipped[/bold yellow] {len(skipped)}")
        if failed := [r for r in self._results if r.status == TestStatus.FAIL]:
            self.console.print(f"  [bold red]Failed[/bold red] {len(failed)}")
        if errored := [r for r in self._results if r.status == TestStatus.ERROR]:
            self.console.print(
                f"  [bold medium_purple3]Errored[/bold medium_purple3] {len(errored)}",
            )

        self.console.print(f"  [bold blue]Total Time[/bold blue] {self._duration}s")

    def _print_slowest(self):
        slowest = sorted(self._results, key=lambda x: x.duration, reverse=True)
        slowest = (
            slowest[: self._print_n_slowest]
            if len(slowest) >= self._print_n_slowest
            else slowest
        )
        if not slowest:
            return
        self.console.print()
        self.console.rule(f" {len(slowest)} Slowest Tests", align="left")
        for result in slowest:
            self.console.print(f"  [bold]{result.name}[/bold] {int(result.duration)}s")

    def _print_time_histogram(self):
        if not self._results:
            return

        self.console.print()
        self.console.rule(f" Test Times Histogram", align="left")

        try:
            import plotext as plt
        except ModuleNotFoundError:
            self.console.print(
                f"  [bold red]Plotext not found[/bold red] make sure it is installed to print histogram"
            )
            return

        def make_plot(width: int, height: int) -> str:
            plt.hist([result.duration for result in self._results])
            plt.plotsize(width, height)
            plt.clear_color()
            return plt.build()

        self.console.print(Padding(RichPlotext(make_plot), (0, 0, 0, 2)))

    def print_summary(self):
        self._print_failed_details()

        if self._print_failure_list:
            self._print_failed_list()

        if self._print_n_slowest:
            self._print_slowest()

        if self._print_duration_hist:
            self._print_time_histogram()

        self._print_result_counts()

        self.console.print()

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

    def __render_exception_arg(self, arg: tuple[str] | str) -> RenderableType:
        match arg:
            case tuple():
                return Group(*(f"• {a}" for a in arg))
            case _:
                return arg

    def __print_exception(self, exc: Exception) -> RenderableType:
        match exc:
            case ExceptionGroup():
                return Panel.fit(
                    Group(*(self.__print_exception(e) for e in exc.exceptions)),
                    title=type(exc).__name__,
                    border_style="yellow",
                )
            case _:
                return Panel.fit(
                    Group(*(self.__render_exception_arg(a) for a in exc.args)),  # pyright: ignore[reportAny]
                    title=type(exc).__name__,
                    border_style="red",
                )

    def print_exception(self, exc: Exception):
        self.console.print(self.__print_exception(exc))
