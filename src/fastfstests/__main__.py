import asyncio
import logging
import os
import sys
import tomllib
from pathlib import Path
from typing import cast

import tyro
from mashumaro.codecs.toml import toml_decode
from rich.console import Console
from tyro._singleton import NonpropagatingMissingType

from parallelrunner.output import Output
from parallelrunner.recording import load_recording, print_comparison
from parallelrunner.test_runner import TestRunner

from .config import CompareConfig, RunConfig
from .fstests import collect_tests
from .supervisors.mkosi import MkosiSupervisor

logger = logging.getLogger(__name__)

_NonpropagatingMissingType = cast(NonpropagatingMissingType, tyro.MISSING_NONPROP)


def main():
    logging.basicConfig()

    config_path = os.getenv("FAST_FSTESTS_CONFIG_PATH") or "config.toml"

    default_compare_config = _NonpropagatingMissingType
    try:
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)
        if results_dir := cast(str, raw.get("output", {}).get("results_dir")):
            default_compare_config = CompareConfig(results_dir=Path(results_dir))
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        pass

    if len(sys.argv) > 1 and sys.argv[1] == "compare":
        config = tyro.cli(
            CompareConfig,
            args=sys.argv[2:],
            prog="ff compare",
            default=default_compare_config,
        )
        compare(config)
        return

    default_config = _NonpropagatingMissingType
    try:
        with open(config_path, "r") as f:
            default_config = toml_decode(f.read(), RunConfig)
    except FileNotFoundError:
        logger.warning("can't find configuration file %s", config_path)
    except tomllib.TOMLDecodeError:
        logger.exception("unable to parse configuration file %s", config_path)

    try:
        config = tyro.cli(RunConfig, default=default_config, prog="ff")
    except SystemExit as e:
        if e.code == 0:
            # Help was printed — append compare help
            try:
                _ = tyro.cli(
                    CompareConfig,
                    args=["--help"],
                    prog="ff compare",
                    default=default_compare_config,
                )
            except SystemExit:
                pass
        raise

    run(config)


def run(config: RunConfig):
    output = Output(
        config.output.results_dir,
        print_failure_list=config.output.print_failure_list,
        print_n_slowest=config.output.print_n_slowest,
        print_duration_hist=config.output.print_duration_hist,
    )

    logging.getLogger().handlers.clear()
    if config.output.results_dir:
        os.makedirs(config.output.results_dir, exist_ok=True)

        if config.output.verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger().addHandler(
            logging.FileHandler(config.output.results_dir.joinpath("logs"), mode="w")
        )

    try:
        tests = list(collect_tests(config))
        if config.test_selection.list:
            print(*[test.name for test in tests], sep="\n")
            return
        if not tests:
            raise ValueError("no tests to run")

        mkosi_machines = list(MkosiSupervisor.from_config(config))
        if forces := config.mkosi.build:
            mkosi_machines[0].build(forces)
        if not mkosi_machines:
            raise ValueError("no supervisors to run tests on")

        if (
            config.test_runner.bpftrace is not None
            and config.test_runner.bpftrace_script is not None
        ):
            raise ValueError("cannot specify --bpftrace and --bpftrace-script")

        runner = TestRunner(
            tests,
            mkosi_machines,
            output,
            config.test_runner.keep_alive,
            config.test_runner.test_timeout,
            config.test_runner.bpftrace or config.test_runner.bpftrace_script,
            config.test_runner.probe_interval,
            config.test_runner.max_supervisor_restarts,
            config.test_runner.dmesg,
        )
        asyncio.run(runner.run())

        if config.output.record is not None:
            output.save_recording(config.output.record)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        output.print_exception(e)
        sys.exit(1)


def resolve_recording(value: int | str, rec_dir: Path) -> tuple[Path, str]:
    match value:
        case int():
            recordings = sorted(rec_dir.iterdir(), key=lambda p: p.stat().st_mtime)
            path = recordings[value]
            return path, path.name
        case str():
            return rec_dir / value, value


def compare(config: CompareConfig):
    console = Console(highlight=False)
    rec_dir = config.results_dir / "recordings"

    if not rec_dir.exists():
        console.print("[red]No recordings found.[/red]")
        console.print(f"  looked in: {rec_dir}")
        sys.exit(1)

    try:
        a_path, a_label = resolve_recording(config.a, rec_dir)
        b_path, b_label = resolve_recording(config.b, rec_dir)
    except (IndexError, FileNotFoundError):
        from parallelrunner.recording import list_recordings

        available = list_recordings(config.results_dir)
        console.print("[red]Recording not found.[/red]")
        if available:
            console.print(f"  available: {', '.join(available)}")
        sys.exit(1)

    if not a_path.exists():
        console.print(f"[red]Recording not found:[/red] {a_label}")
        console.print(f"  looked in: {a_path}")
        sys.exit(1)
    if not b_path.exists():
        console.print(f"[red]Recording not found:[/red] {b_label}")
        console.print(f"  looked in: {b_path}")
        sys.exit(1)

    a = load_recording(a_path)
    b = load_recording(b_path)
    print_comparison(console, a, b, a_label, b_label)


if __name__ == "__main__":
    main()
