import asyncio
import logging
import os
import sys
import tomllib

import tyro
from mashumaro.codecs.toml import toml_decode
from rich.console import Console
from tyro.conf import CascadeSubcommandArgs

from parallelrunner.output import Output
from parallelrunner.recording import load_recording, print_comparison
from parallelrunner.test_runner import TestRunner

from .config import CompareConfig, Config, RunConfig
from .fstests import collect_tests
from .supervisors.mkosi import MkosiSupervisor

logger = logging.getLogger(__name__)


def main():
    logging.basicConfig()

    config_path = os.getenv("FAST_FSTESTS_CONFIG_PATH") or "config.toml"
    default_run_config = None
    try:
        with open(config_path, "r") as f:
            default_run_config = toml_decode(f.read(), RunConfig)
    except FileNotFoundError:
        logger.warning("can't find configuration file %s", config_path)
    except tomllib.TOMLDecodeError:
        logger.exception("unable to parse configuration file %s", config_path)

    default_config = Config(command=default_run_config) if default_run_config else None
    config = tyro.cli(
        Config, default=default_config, prog="ff", config=(CascadeSubcommandArgs,)
    )

    match config.command:
        case RunConfig():
            run(config.command)
        case CompareConfig():
            compare(config.command)


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


def compare(config: CompareConfig):
    console = Console(highlight=False)
    rec_dir = config.results_dir / "recordings"

    a_path = rec_dir / config.a
    b_path = rec_dir / config.b

    if not a_path.exists():
        console.print(f"[red]Recording not found:[/red] {config.a}")
        console.print(f"  looked in: {a_path}")
        sys.exit(1)
    if not b_path.exists():
        console.print(f"[red]Recording not found:[/red] {config.b}")
        console.print(f"  looked in: {b_path}")
        sys.exit(1)

    a = load_recording(a_path)
    b = load_recording(b_path)
    print_comparison(console, a, b, config.a, config.b)


if __name__ == "__main__":
    main()
