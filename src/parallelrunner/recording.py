import os
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.rule import Rule

from .test import TestStatus


@dataclass
class RecordedResult:
    status: TestStatus
    duration: float
    retries: int = 0


def _read_result(test_dir: Path) -> RecordedResult | None:
    status_file = test_dir / "status"
    duration_file = test_dir / "duration"
    if not status_file.exists() or not duration_file.exists():
        return None
    retries_file = test_dir / "retries"
    return RecordedResult(
        status=TestStatus[status_file.read_text().strip()],
        duration=float(duration_file.read_text().strip()),
        retries=int(retries_file.read_text().strip()) if retries_file.exists() else 0,
    )


def load_recording(rec_dir: Path) -> dict[str, RecordedResult]:
    results: dict[str, RecordedResult] = {}

    for entry in os.scandir(rec_dir):
        if not entry.is_dir():
            continue

        test_dir = Path(entry.path)

        # Try reading directly (flat test name).
        result = _read_result(test_dir)
        if result is not None:
            results[entry.name] = result
            continue

        # Handle nested test names like "btrfs/001".
        for sub_entry in os.scandir(test_dir):
            if not sub_entry.is_dir():
                continue
            sub_result = _read_result(Path(sub_entry.path))
            if sub_result is not None:
                results[f"{entry.name}/{sub_entry.name}"] = sub_result

    return results


def list_recordings(results_dir: Path) -> list[str]:
    rec_dir = results_dir / "recordings"
    if not rec_dir.exists():
        return []
    return sorted(
        entry.name for entry in os.scandir(rec_dir) if entry.is_dir()
    )


def resolve_recording(value: int | str, results_dir: Path) -> tuple[Path, str]:
    rec_dir = results_dir / "recordings"
    match value:
        case int():
            recordings = sorted(rec_dir.iterdir(), key=lambda p: p.stat().st_mtime)
            path = recordings[value]
            return path, path.name
        case str():
            return rec_dir / value, value


def print_comparison(
    console: Console,
    a: dict[str, RecordedResult],
    b: dict[str, RecordedResult],
    label_a: str,
    label_b: str,
):
    all_tests = sorted(set(a.keys()) | set(b.keys()))

    regressions: list[tuple[str, str, str]] = []
    fixes: list[tuple[str, str, str]] = []
    new_tests: list[str] = []
    removed_tests: list[str] = []
    timing: list[tuple[str, int]] = []
    flaky: list[tuple[str, int]] = []

    for name in all_tests:
        ra, rb = a.get(name), b.get(name)
        if ra is None:
            new_tests.append(name)
            if rb is not None and rb.retries > 0:
                flaky.append((name, rb.retries))
            continue
        if rb is None:
            removed_tests.append(name)
            continue

        if ra.status != rb.status:
            old = ra.status.name.lower()
            new = rb.status.name.lower()
            if rb.status in (TestStatus.FAIL, TestStatus.ERROR):
                regressions.append((name, old, new))
            elif ra.status in (TestStatus.FAIL, TestStatus.ERROR):
                fixes.append((name, old, new))

        delta = int(rb.duration - ra.duration)
        if abs(delta) >= 5:
            timing.append((name, delta))

        if rb.retries > 0 and rb.status == TestStatus.PASS:
            flaky.append((name, rb.retries))

    console.print()
    console.print(Rule(f" {label_a} vs {label_b}", align="left"))

    if regressions:
        console.print(f"  [bold red]Regressions[/bold red] {len(regressions)}")
        for name, old, new in regressions:
            console.print(f"    {name}  {old} → {new}")

    if fixes:
        console.print(f"  [bold green]Fixes[/bold green] {len(fixes)}")
        for name, old, new in fixes:
            console.print(f"    {name}  {old} → {new}")

    if flaky:
        console.print(f"  [bold yellow]Flaky[/bold yellow] {len(flaky)}")
        for name, retries in flaky:
            console.print(f"    {name}  {retries} {'retry' if retries == 1 else 'retries'}")

    if new_tests:
        new_non_flaky = [n for n in new_tests if not any(f[0] == n for f in flaky)]
        if new_non_flaky:
            console.print(f"  [bold blue]New in {label_b}[/bold blue] {len(new_non_flaky)}")
            for name in new_non_flaky:
                console.print(f"    {name}")

    if removed_tests:
        console.print(f"  [bold yellow]Removed from {label_b}[/bold yellow] {len(removed_tests)}")
        for name in removed_tests:
            console.print(f"    {name}")

    if timing:
        timing.sort(key=lambda t: t[1], reverse=True)
        console.print(f"  [bold]Timing changes[/bold] (>= 5s)")
        for name, delta in timing:
            sign = "+" if delta > 0 else ""
            color = "red" if delta > 0 else "green"
            console.print(f"    [{color}]{sign}{delta}s[/{color}]  {name}")

    if not regressions and not fixes and not new_tests and not removed_tests and not timing and not flaky:
        console.print("  No differences found.")

    console.print()
