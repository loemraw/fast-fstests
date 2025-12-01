import asyncio
import logging
import os
import sys
import tomllib

import tyro
from mashumaro.codecs.toml import toml_decode

from parallelrunner.output import Output
from parallelrunner.test_runner import TestRunner

from .config import Config
from .fstests import collect_tests
from .supervisors.mkosi import MkosiSupervisor

logger = logging.getLogger(__name__)


def main():
    logging.basicConfig(level=logging.WARNING)

    config_path = os.getenv("FAST_FSTESTS_CONFIG_PATH") or "config.toml"
    default_config = None
    try:
        with open(config_path, "r") as f:
            default_config = toml_decode(f.read(), Config)
    except FileNotFoundError:
        logger.warning("can't find configuration file %s", config_path)
    except tomllib.TOMLDecodeError:
        logger.exception("unable to parse configuration file %s", config_path)

    config = tyro.cli(Config, default=default_config, prog="ff")
    output = Output(
        config.output.results_dir,
        print_failure_list=config.output.print_failure_list,
        print_n_slowest=config.output.print_n_slowest,
        print_duration_hist=config.output.print_duration_hist,
    )

    try:
        tests = list(collect_tests(config))
        if config.test_selection.list:
            print(*[test.name for test in tests], sep="\n")
            return
        assert len(tests) > 0, "no tests to run"

        mkosi_machines = list(MkosiSupervisor.from_config(config))
        if forces := config.mkosi.build:
            mkosi_machines[0].build(forces)
        assert len(mkosi_machines) > 0, "no supervisors to run tests on"

        assert (
            config.test_runner.bpftrace is None
            or config.test_runner.bpftrace_script is None
        ), "cannot specify --bpftrace and --bpftrace-script"

        runner = TestRunner(
            tests,
            mkosi_machines,
            output,
            config.test_runner.keep_alive,
            config.test_runner.test_timeout,
            config.test_runner.bpftrace or config.test_runner.bpftrace_script,
        )
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        output.print_exception(e)
        sys.exit(1)


if __name__ == "__main__":
    main()
