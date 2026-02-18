import logging
import os
import shutil
import statistics
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryFile

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
        print_test_regressions: int = 0,
    ):
        self.console: Console = Console(highlight=False)
        self.results_dir: Path | None = results_dir

        self._print_failure_list: bool = print_failure_list
        self._print_n_slowest: int = print_n_slowest
        self._print_duration_hist: bool = print_duration_hist
        self._print_test_regressions: int = print_test_regressions

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
        self._reset_latest()

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
            if self.results_dir is None:
                with TemporaryFile("wb+") as stdout:
                    with TemporaryFile("wb+") as stderr:
                        yield stdout, stderr
            else:
                path = self._get_test_path(test)
                latest = self._get_latest_path(test)

                with open(path.joinpath("stdout"), "wb+") as stdout:
                    self._link("stdout", path, latest)
                    with open(path.joinpath("stderr"), "wb+") as stderr:
                        self._link("stderr", path, latest)
                        yield stdout, stderr
        finally:
            self._test_progress.remove_task(task_id)

    def _get_test_path(self, test: Test) -> Path:
        assert self.results_dir is not None
        path = self.results_dir.joinpath("tests", test.name, test.id)
        os.makedirs(path, exist_ok=True)
        return path

    def _get_latest_path(self, test: Test) -> Path:
        assert self.results_dir is not None
        path = self.results_dir.joinpath("latest", test.name)
        os.makedirs(path, exist_ok=True)
        return path
    
    def _link(self, filename: str, path: Path, destination: Path):
        destination = destination.joinpath(filename)
        if destination.is_file():
            os.remove(destination)
        os.symlink(
            path.absolute().joinpath(filename),
            destination,
        )

    def _reset_latest(self):
        if self.results_dir is None:
            return

        path = self.results_dir.joinpath("latest")
        if path.exists():
            shutil.rmtree(path)

    def finished_test(self, test: Test, result: TestResult):
        self._results.append(result)

        self._overall_test_progress.advance(self._overall_test_task_id)
        self._log_result(test, result)

    def _log_result(self, test: Test, result: TestResult):
        logger.debug(f"summary for test {test}: {result.summary}")
        self.console.print(
            self._test_status_output(result),
            result.name,
            self._test_duration_output(test, result),
            f"[dim]{result.summary}" if result.summary else "",
        )

        if self.results_dir is None:
            return

        path = self._get_test_path(test)
        latest = self._get_latest_path(test)

        with open(path.joinpath("retcode"), "w") as f:
            _ = f.write(str(result.retcode))
        self._link("retcode", path, latest)

        with open(path.joinpath("duration"), "w") as f:
            _ = f.write(str(result.duration))
        self._link("duration", path, latest)

        with open(path.joinpath("status"), "w") as f:
            _ = f.write(str(result.status.name))
        self._link("status", path, latest)

        if result.artifacts:
            path = path.joinpath("artifacts")
            os.makedirs(path, exist_ok=True)
            latest = latest.joinpath("artifacts")
            os.makedirs(latest, exist_ok=True)

            for name, value in result.artifacts.items():
                with open(path.joinpath(name), "wb") as f:
                    _ = f.write(value)
                self._link(name, path, latest)

    def _test_status_output(self, result: TestResult) -> str:
        match result.status:
            case TestStatus.PASS:
                return "  [bold green]pass[/bold green]"
            case TestStatus.FAIL:
                return "  [bold red]fail[/bold red]"
            case TestStatus.SKIP:
                return "  [bold yellow]skip[/bold yellow]"
            case TestStatus.ERROR:
                return "  [bold medium_purple3]error[/bold medium_purple3]"

    def _test_duration_output(self, test: Test, result: TestResult) -> str:
        logger.debug(f"printing test regressions? {self._print_test_regressions}")
        duration = f"[yellow]{str(timedelta(seconds=int(result.duration)))}"
        if self._print_test_regressions <= 0:
            return duration

        times = [
            float(path.read_text())
            for path in [
                Path(path).joinpath("duration")
                for path in os.scandir(self._get_test_path(test).parent)
                if path.is_dir()
            ]
            if path.is_file()
        ]

        median = statistics.median(times) if times else result.duration
        deviation = abs(result.duration - median)
        bounded_deviation = (
            min(float(self._print_test_regressions), deviation)
            / self._print_test_regressions
        )
        if result.duration > median:
            g = b = int(255 * (1 - bounded_deviation))
            color = f"#ff{g:02x}{b:02x}"
        else:
            r = b = int(255 * (1 - bounded_deviation))
            color = f"#{r:02x}ff{b:02x}"

        return f"{duration} [{color}]\\[p50 {str(timedelta(seconds=int(median)))}]"

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
        if tests := [
            r for r in self._results if r.status in (TestStatus.FAIL, TestStatus.ERROR)
        ]:
            self.console.print()
            self.console.print(Rule(" Failure List", align="left"))
            self.console.print(
                *set(test.name for test in tests),
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
        slowest = sorted(
            self._results,
            key=lambda x: x.duration,
            reverse=True,
        )
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

    def __print_exception(self, exc: Exception) -> RenderableType:
        parts: list[RenderableType] = []

        match exc:
            case ExceptionGroup():
                return Panel.fit(
                    Group(*(self.__print_exception(e) for e in exc.exceptions)),
                    title=type(exc).__name__,
                    border_style="yellow",
                )
            case _ if exc.args:
                parts.extend(str(a) for a in exc.args)
            case _:
                parts.append(str(exc) or type(exc).__name__)

        for note in getattr(exc, "__notes__", []):
            parts.append(f"[dim]{note}[/dim]")

        return Panel.fit(
            Group(*parts),
            title=type(exc).__name__,
            border_style="red",
        )

    def print_exception(self, exc: Exception):
        self.console.print(self.__print_exception(exc))

    @contextmanager
    def log_bpftrace(self, test: Test):
        if self.results_dir is None:
            yield (None, None)
            return

        path = self._get_test_path(test)
        latest = self._get_latest_path(test)
        with open(path.joinpath("bpftrace-stdout"), "wb+") as stdout:
            self._link("bpftrace-stdout", path, latest)
            with open(path.joinpath("bpftrace-stderr"), "wb+") as stderr:
                self._link("bpftrace-stderr", path, latest)
                yield (stdout, stderr)

    def supervisor_died(self, supervisor: Supervisor):
        self.console.print(f"  [bold red]dead[/bold red] {supervisor}")
