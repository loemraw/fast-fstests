import logging
import os
import shutil
import statistics
from collections import Counter
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

from parallelrunner.supervisor import Supervisor
from parallelrunner.test import Test, TestResult, TestStatus

from .rich_plotext import RichPlotext

logger = logging.getLogger(__name__)

_STATUS_STYLES: dict[TestStatus, tuple[str, str]] = {
    TestStatus.PASS: ("green", "Passed"),
    TestStatus.SKIP: ("yellow", "Skipped"),
    TestStatus.FAIL: ("red", "Failed"),
    TestStatus.ERROR: ("medium_purple3", "Errored"),
}


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
        self._duration: int = 0

        supervisor_progress = self._create_supervisor_progress()
        self._supervisor_live: Live = supervisor_progress[0]
        self._supervisor_progress: Progress = supervisor_progress[1]
        self._supervisors_progress: Progress = supervisor_progress[2]
        self._supervisor_task_id: TaskID = supervisor_progress[3]

        test_progress = self._create_test_progress()
        self._test_live: Live = test_progress[0]
        self._test_progress: Progress = test_progress[1]
        self._overall_test_progress: Progress = test_progress[2]
        self._overall_test_task_id: TaskID = test_progress[3]

    def _create_supervisor_progress(
        self,
    ) -> tuple[Live, Progress, Progress, TaskID]:
        overall = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(complete_style="green", finished_style="green"),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("({task.completed}/{task.total})"),
            TimeElapsedColumn(),
            console=self.console,
        )
        task_id = overall.add_task("Spawning supervisors...")
        individual = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            TimeElapsedColumn(),
            console=self.console,
        )
        live = Live(Group(individual, overall), console=self.console)
        return live, individual, overall, task_id

    def _create_test_progress(self) -> tuple[Live, Progress, Progress, TaskID]:
        overall = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]Running tests..."),
            BarColumn(complete_style="green", finished_style="green"),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("({task.completed}/{task.total})"),
            TimeElapsedColumn(),
            console=self.console,
        )
        task_id = overall.add_task("overall", start=False)
        individual = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            TimeElapsedColumn(),
            console=self.console,
        )
        live = Live(Group(individual, overall), console=self.console)
        return live, individual, overall, task_id

    # --- Supervisor lifecycle ---

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
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            self._supervisor_progress.remove_task(task_id)
            self._supervisors_progress.advance(self._supervisor_task_id)
            if failed:
                self.console.print(f"  [bold red]failed[/bold red] {supervisor}")
            elif supervisor.exited:
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

    def supervisor_died(self, supervisor: Supervisor):
        self.console.print(f"  [bold red]dead[/bold red] {supervisor}")

    # --- Test execution ---

    @contextmanager
    def running_tests(self, num_tests: int):
        self._reset_latest()

        self.console.print()
        self.console.print(Rule(title=" Testing", align="left"))
        self._overall_test_progress.reset(self._overall_test_task_id, total=num_tests)

        start_time = datetime.now()
        try:
            with self._test_live:
                yield self
        finally:
            self._duration = (datetime.now() - start_time).seconds

    @contextmanager
    def running_test(self, test: Test):
        task_id = self._test_progress.add_task(test.name)
        try:
            with self._open_test_outputs(test) as (stdout, stderr):
                yield stdout, stderr
        finally:
            self._test_progress.remove_task(task_id)

    @contextmanager
    def _open_test_outputs(self, test: Test):
        if self.results_dir is None:
            with TemporaryFile("wb+") as stdout, TemporaryFile("wb+") as stderr:
                yield stdout, stderr
            return

        path = self._get_test_path(test)
        latest = self._get_latest_path(test)
        with open(path / "stdout", "wb+") as stdout:
            self._link("stdout", path, latest)
            with open(path / "stderr", "wb+") as stderr:
                self._link("stderr", path, latest)
                yield stdout, stderr

    def finished_test(self, test: Test, result: TestResult):
        self._results.append(result)
        self._overall_test_progress.advance(self._overall_test_task_id)
        self._print_result(test, result)
        self._save_result(test, result)

    @contextmanager
    def log_bpftrace(self, test: Test):
        if self.results_dir is None:
            yield (None, None)
            return

        path = self._get_test_path(test)
        latest = self._get_latest_path(test)
        with open(path / "bpftrace-stdout", "wb+") as stdout:
            self._link("bpftrace-stdout", path, latest)
            with open(path / "bpftrace-stderr", "wb+") as stderr:
                self._link("bpftrace-stderr", path, latest)
                yield (stdout, stderr)

    # --- Result persistence ---

    def _get_test_path(self, test: Test) -> Path:
        assert self.results_dir is not None
        path = self.results_dir / "tests" / test.name / test.id
        os.makedirs(path, exist_ok=True)
        return path

    def _get_latest_path(self, test: Test) -> Path:
        assert self.results_dir is not None
        path = self.results_dir / "latest" / test.name
        os.makedirs(path, exist_ok=True)
        return path

    def _link(self, filename: str, path: Path, destination: Path):
        dest = destination / filename
        if dest.is_file():
            os.remove(dest)
        os.symlink(path.absolute() / filename, dest)

    def _reset_latest(self):
        if self.results_dir is None:
            return
        path = self.results_dir / "latest"
        if path.exists():
            shutil.rmtree(path)

    def _save_result(self, test: Test, result: TestResult):
        if self.results_dir is None:
            return

        path = self._get_test_path(test)
        latest = self._get_latest_path(test)

        for name, value in [
            ("retcode", str(result.retcode)),
            ("duration", str(result.duration)),
            ("status", result.status.name),
        ]:
            _ = path.joinpath(name).write_text(value)
            self._link(name, path, latest)

        if result.artifacts:
            artifact_path = path / "artifacts"
            artifact_latest = latest / "artifacts"
            os.makedirs(artifact_path, exist_ok=True)
            os.makedirs(artifact_latest, exist_ok=True)

            for name, value in result.artifacts.items():
                _ = artifact_path.joinpath(name).write_bytes(value)
                self._link(name, artifact_path, artifact_latest)

    # --- Console output ---

    def _print_result(self, test: Test, result: TestResult):
        logger.debug("summary for test %s: %s", test, result.summary)
        style, _ = _STATUS_STYLES[result.status]
        self.console.print(
            f"  [bold {style}]{result.status.name.lower()}[/bold {style}]",
            result.name,
            self._format_duration(test, result),
            f"[dim]{result.summary}" if result.summary else "",
        )

    def _format_duration(self, test: Test, result: TestResult) -> str:
        duration = f"[yellow]{timedelta(seconds=int(result.duration))}"
        if self._print_test_regressions <= 0:
            return duration

        duration_files = [
            Path(entry).joinpath("duration")
            for entry in os.scandir(self._get_test_path(test).parent)
            if entry.is_dir()
        ]
        times = [
            float(f.read_text()) for f in duration_files if f.is_file()
        ]

        median = statistics.median(times) if times else result.duration
        deviation = abs(result.duration - median)
        bounded = min(float(self._print_test_regressions), deviation) / self._print_test_regressions

        if result.duration > median:
            g = b = int(255 * (1 - bounded))
            color = f"#ff{g:02x}{b:02x}"
        else:
            r = b = int(255 * (1 - bounded))
            color = f"#{r:02x}ff{b:02x}"

        return f"{duration} [{color}]\\[p50 {timedelta(seconds=int(median))}]"

    # --- Summary ---

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

    def _print_failed_details(self):
        for result in self._results:
            if result.status == TestStatus.FAIL:
                label = "Failed"
            elif result.status == TestStatus.ERROR:
                label = "Error"
            else:
                continue

            header = [label, result.name]
            if self.results_dir is not None:
                header.append("@")
                header.append(str(
                    self.results_dir / result.name
                    / result.timestamp.strftime("%Y-%m-%d_%H-%M-%S_%f")
                ))

            self.console.print()
            self.console.print(Rule(f" {' '.join(header)}", align="left", style="red"))

            if result.stdout:
                self.console.print(
                    Panel.fit(result.stdout.decode(), title="stdout", title_align="left")
                )
            if result.stderr:
                self.console.print(
                    Panel.fit(result.stderr.decode(), title="stderr", title_align="left")
                )

    def _print_failed_list(self):
        failed = [
            r for r in self._results if r.status in (TestStatus.FAIL, TestStatus.ERROR)
        ]
        if not failed:
            return
        self.console.print()
        self.console.print(Rule(" Failure List", align="left"))
        self.console.print(*set(r.name for r in failed), soft_wrap=True)

    def _print_result_counts(self):
        self.console.print()
        self.console.print(Rule(" Summary", align="left"))

        counts = Counter(r.status for r in self._results)
        for status, (style, label) in _STATUS_STYLES.items():
            if count := counts.get(status, 0):
                self.console.print(f"  [bold {style}]{label}[/bold {style}] {count}")

        self.console.print(f"  [bold blue]Total Time[/bold blue] {self._duration}s")

    def _print_slowest(self):
        slowest = sorted(self._results, key=lambda r: r.duration, reverse=True)
        slowest = slowest[:self._print_n_slowest]
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
        self.console.rule(" Test Times Histogram", align="left")

        try:
            import plotext as plt
        except ModuleNotFoundError:
            self.console.print(
                "  [bold red]Plotext not found[/bold red] make sure it is installed to print histogram"
            )
            return

        def make_plot(width: int, height: int) -> str:
            plt.hist([r.duration for r in self._results])
            plt.plotsize(width, height)
            plt.clear_color()
            return plt.build()

        self.console.print(Padding(RichPlotext(make_plot), (0, 0, 0, 2)))

    # --- Misc ---

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

    def _render_exception(self, exc: Exception) -> RenderableType:
        parts: list[RenderableType] = []

        match exc:
            case ExceptionGroup():
                return Panel.fit(
                    Group(*(self._render_exception(e) for e in exc.exceptions)),
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
        self.console.print(self._render_exception(exc))
