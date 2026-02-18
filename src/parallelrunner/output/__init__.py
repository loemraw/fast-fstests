import logging
import os
import shutil
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
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


@dataclass
class BaselineResult:
    status: TestStatus
    duration: float


class Output:
    def __init__(
        self,
        results_dir: Path | None,
        print_failure_list: bool = False,
        print_n_slowest: int = 0,
        print_duration_hist: bool = False,
        record: bool = False,
        diff: bool = False,
    ):
        self.console: Console = Console(highlight=False)
        self.results_dir: Path | None = results_dir

        self._print_failure_list: bool = print_failure_list
        self._print_n_slowest: int = print_n_slowest
        self._print_duration_hist: bool = print_duration_hist
        self._record: bool = record
        self._diff: bool = diff

        self._results: list[TestResult] = []
        self._retries: dict[str, int] = {}
        self._duration: int = 0
        self._baseline: dict[str, BaselineResult] = (
            self._load_baseline() if diff else {}
        )

        if diff and not self._baseline:
            raise ValueError(
                "no baseline found, run with --record first to create one"
            )

        test_progress = self._create_test_progress()
        self._test_live: Live = test_progress[0]
        self._test_progress: Progress = test_progress[1]
        self._overall_test_progress: Progress = test_progress[2]
        self._overall_test_task_id: TaskID = test_progress[3]

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

    def _live_print(self, *args: object):
        """Print while Live display is active.

        Forces a refresh first so the Live display's internal line count
        matches the current number of progress tasks, preventing stale
        cursor positioning that leaves ghost spinner lines in scrollback.
        """
        self._test_live.refresh()
        self.console.print(*args)

    # --- Supervisor lifecycle ---

    @contextmanager
    def spawning_supervisor(self, supervisor: Supervisor):
        task_id = self._test_progress.add_task(f"> spawning {supervisor!r}")
        failed = False
        start = time.monotonic()
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            duration = timedelta(seconds=int(time.monotonic() - start))
            self._test_progress.remove_task(task_id)
            if failed:
                self._live_print(f"  > [bold red]failed[/bold red] {supervisor} [yellow]{duration}")
            else:
                self._live_print(f"  > [bold green]spawn[/bold green] {supervisor} [yellow]{duration}")

    @contextmanager
    def respawning_supervisor(self, supervisor: Supervisor):
        task_id = self._test_progress.add_task(f"> respawning {supervisor!r}")
        start = time.monotonic()
        try:
            yield
        finally:
            duration = timedelta(seconds=int(time.monotonic() - start))
            self._test_progress.remove_task(task_id)
            self._live_print(f"  > [bold green]respawn[/bold green] {supervisor} [yellow]{duration}")

    @contextmanager
    def exiting_supervisor(self, supervisor: Supervisor):
        task_id = self._test_progress.add_task(f"> exiting {supervisor!r}")
        start = time.monotonic()
        try:
            yield
        finally:
            duration = timedelta(seconds=int(time.monotonic() - start))
            self._test_progress.remove_task(task_id)
            self._live_print(f"  > [bold green]exit[/bold green] {supervisor} [yellow]{duration}")

    def supervisor_died(self, supervisor: Supervisor, test_name: str | None = None):
        msg = f"  > [bold red]dead[/bold red] {supervisor}"
        if test_name is not None:
            msg += f" [dim]was running {test_name}"
        self._live_print(msg)

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

    def record_retry(self, test: Test, result: TestResult):
        self._retries[test.name] = self._retries.get(test.name, 0) + 1
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

    @contextmanager
    def log_dmesg(self, test: Test):
        if self.results_dir is None:
            yield None
            return

        path = self._get_test_path(test)
        latest = self._get_latest_path(test)
        with open(path / "dmesg", "wb+") as f:
            self._link("dmesg", path, latest)
            yield f

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

    def get_artifact_path(self, test: Test) -> Path | None:
        if self.results_dir is None:
            return None
        path = self._get_test_path(test) / "artifacts"
        os.makedirs(path, exist_ok=True)
        return path

    def link_artifacts(self, test: Test):
        if self.results_dir is None:
            return
        path = self._get_test_path(test) / "artifacts"
        if not path.exists():
            return
        latest = self._get_latest_path(test) / "artifacts"
        os.makedirs(latest, exist_ok=True)
        for name in os.listdir(path):
            self._link(name, path, latest)

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

    # --- Console output ---

    def _print_result(self, test: Test, result: TestResult):
        logger.debug("summary for test %s: %s", test, result.summary)
        style, _ = _STATUS_STYLES[result.status]
        parts: list[str] = [
            f"  [bold {style}]{result.status.name.lower()}[/bold {style}]",
            result.name,
        ]

        if result.status != TestStatus.ERROR:
            parts.append(f"[yellow]{timedelta(seconds=int(result.duration))}")

        if self._diff:
            parts.append(self._format_diff(result))

        if result.summary:
            parts.append(f"[dim]{result.summary}")

        self._live_print(*parts)

    def _format_diff(self, result: TestResult) -> str:
        baseline = self._baseline.get(result.name)
        if baseline is None:
            return "[dim]\\[new]"

        if baseline.status != result.status:
            old = baseline.status.name.lower()
            new = result.status.name.lower()
            is_regression = result.status in (TestStatus.FAIL, TestStatus.ERROR)
            color = "red" if is_regression else "green"
            return f"[bold {color}]\\[{old} → {new}]"

        delta = int(result.duration - baseline.duration)
        if delta == 0:
            return ""
        sign = "+" if delta > 0 else ""
        color = "red" if delta > 0 else "green"
        return f"[{color}]\\[{sign}{delta}s]"

    # --- Summary ---

    def print_summary(self):
        self._print_failed_details()

        if self._print_failure_list:
            self._print_failed_list()

        if self._print_n_slowest:
            self._print_slowest()

        if self._print_duration_hist:
            self._print_time_histogram()

        if self._retries:
            self._print_retries()

        self._print_result_counts()

        if self._diff:
            self._print_diff_summary()

        if self._record:
            self._save_baseline()

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
            if self.results_dir is not None:
                dmesg_path = self.results_dir / "latest" / result.name / "dmesg"
                if dmesg_path.exists():
                    dmesg = dmesg_path.read_bytes().decode(errors="replace")
                    if dmesg.strip():
                        self.console.print(
                            Panel.fit(dmesg, title="dmesg", title_align="left")
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

        if total_retries := sum(self._retries.values()):
            self.console.print(f"  [bold cyan]Retried[/bold cyan] {total_retries}")

        self.console.print(f"  [bold blue]Total Time[/bold blue] {self._duration}s")

    def _print_retries(self):
        self.console.print()
        self.console.print(Rule(" Retries", align="left"))
        for name, count in sorted(self._retries.items()):
            self.console.print(f"  {name}  {count} {'attempt' if count == 1 else 'attempts'}")

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

    def _print_diff_summary(self):
        regressions: list[tuple[str, str, str]] = []
        fixes: list[tuple[str, str, str]] = []
        new_tests: list[str] = []

        for result in self._results:
            baseline = self._baseline.get(result.name)
            if baseline is None:
                new_tests.append(result.name)
            elif baseline.status != result.status:
                old = baseline.status.name.lower()
                new = result.status.name.lower()
                if result.status in (TestStatus.FAIL, TestStatus.ERROR):
                    regressions.append((result.name, old, new))
                elif baseline.status in (TestStatus.FAIL, TestStatus.ERROR):
                    fixes.append((result.name, old, new))

        if not regressions and not fixes and not new_tests:
            return

        self.console.print()
        self.console.print(Rule(" Diff", align="left"))

        if regressions:
            self.console.print(
                f"  [bold red]Regressions[/bold red] {len(regressions)}"
            )
            for name, old, new in regressions:
                self.console.print(f"    {name}  {old} → {new}")

        if fixes:
            self.console.print(f"  [bold green]Fixes[/bold green] {len(fixes)}")
            for name, old, new in fixes:
                self.console.print(f"    {name}  {old} → {new}")

        if new_tests:
            self.console.print(f"  [bold blue]New[/bold blue] {len(new_tests)}")

    # --- Baseline ---

    def _load_baseline(self) -> dict[str, BaselineResult]:
        if self.results_dir is None:
            return {}

        baseline_dir = self.results_dir / "baseline"
        if not baseline_dir.exists():
            return {}

        baseline: dict[str, BaselineResult] = {}
        for entry in os.scandir(baseline_dir):
            if not entry.is_dir():
                continue

            # Handle nested test names like "btrfs/001"
            test_dir = Path(entry.path)
            status_file = test_dir / "status"
            duration_file = test_dir / "duration"

            # Check for nested subdirectories (e.g., baseline/btrfs/001/)
            if not status_file.exists():
                for sub_entry in os.scandir(test_dir):
                    if not sub_entry.is_dir():
                        continue
                    sub_dir = Path(sub_entry.path)
                    sub_status = sub_dir / "status"
                    sub_duration = sub_dir / "duration"
                    if sub_status.exists() and sub_duration.exists():
                        name = f"{entry.name}/{sub_entry.name}"
                        baseline[name] = BaselineResult(
                            status=TestStatus[sub_status.read_text().strip()],
                            duration=float(sub_duration.read_text().strip()),
                        )
                continue

            if duration_file.exists():
                baseline[entry.name] = BaselineResult(
                    status=TestStatus[status_file.read_text().strip()],
                    duration=float(duration_file.read_text().strip()),
                )

        return baseline

    def _save_baseline(self):
        if self.results_dir is None:
            return

        latest = self.results_dir / "latest"
        baseline = self.results_dir / "baseline"

        if not latest.exists():
            logger.warning("no latest results to save as baseline")
            return

        if baseline.exists():
            shutil.rmtree(baseline)

        _ = shutil.copytree(latest, baseline, symlinks=True)
        self.console.print(f"  [dim]Baseline saved to {baseline}")

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
