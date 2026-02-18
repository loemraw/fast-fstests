import logging
import os
import shutil
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryFile

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markup import escape
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
    ):
        self.console: Console = Console(highlight=False)
        self.results_dir: Path | None = results_dir

        self._print_failure_list: bool = print_failure_list
        self._print_n_slowest: int = print_n_slowest
        self._print_duration_hist: bool = print_duration_hist

        self._results: list[TestResult] = []
        self._retries: dict[str, int] = {}
        self._duration: int = 0

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
            msg += f"  [dim]was running {test_name}"
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
        self._link_latest(test)
        with open(path / "stdout", "wb+") as stdout:
            with open(path / "stderr", "wb+") as stderr:
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
        with open(path / "bpftrace-stdout", "wb+") as stdout:
            with open(path / "bpftrace-stderr", "wb+") as stderr:
                yield (stdout, stderr)

    @contextmanager
    def log_dmesg(self, test: Test):
        if self.results_dir is None:
            yield None
            return

        path = self._get_test_path(test)
        with open(path / "dmesg", "wb+") as f:
            yield f

    # --- Result persistence ---

    def _get_test_path(self, test: Test) -> Path:
        assert self.results_dir is not None
        path = self.results_dir / "tests" / test.name / test.id
        os.makedirs(path, exist_ok=True)
        return path

    def _link_latest(self, test: Test):
        if self.results_dir is None:
            return
        test_path = self._get_test_path(test)
        latest_link = self.results_dir / "latest" / test.name
        if latest_link.is_symlink():
            latest_link.unlink()
        os.makedirs(latest_link.parent, exist_ok=True)
        os.symlink(os.path.relpath(test_path, latest_link.parent), latest_link)

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

    def _save_result(self, test: Test, result: TestResult):
        if self.results_dir is None:
            return

        path = self._get_test_path(test)

        for name, value in [
            ("retcode", str(result.retcode)),
            ("duration", str(result.duration)),
            ("status", result.status.name),
        ]:
            _ = path.joinpath(name).write_text(value)

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

        if result.summary:
            parts.append(f" [dim]{escape(result.summary)}")

        self._live_print(*parts)

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

    # --- Recordings ---

    def save_recording(self, label: str):
        if self.results_dir is None:
            return
        rec_dir = self.results_dir / "recordings" / label
        os.makedirs(rec_dir, exist_ok=True)
        for result in self._results:
            test_path = (
                self.results_dir
                / "tests"
                / result.name
                / result.timestamp.strftime("%Y-%m-%d_%H-%M-%S_%f")
            )
            link = rec_dir / result.name
            if link.is_symlink():
                link.unlink()
            os.makedirs(link.parent, exist_ok=True)
            os.symlink(os.path.relpath(test_path, link.parent), link)
        self.console.print(f"  [dim]Recording saved: {label}")

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
            parts.append(f"[dim]{escape(str(note))}[/dim]")

        return Panel.fit(
            Group(*parts),
            title=type(exc).__name__,
            border_style="red",
        )

    def print_exception(self, exc: Exception):
        self.console.print(self._render_exception(exc))
