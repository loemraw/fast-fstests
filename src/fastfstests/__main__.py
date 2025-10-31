import asyncio
import logging
import os
import sys
import tomllib

import tyro
from mashumaro.codecs.toml import toml_decode

from parallelrunner.output import Output
from parallelrunner.supervisor import Supervisor
from parallelrunner.test_runner import TestRunner

from .config import Config
from .fstests import collect_tests
from .supervisors.mkosi import MkosiSupervisor

logger = logging.getLogger(__name__)


def main():
    logging.basicConfig(level=logging.DEBUG)

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
    output = Output(config.results_dir)

    try:
        machines: list[Supervisor] = []
        machines.extend(MkosiSupervisor.from_config(config))
        assert len(machines) > 0, "no supervisors to run tests on"

        tests = list(collect_tests(config))
        assert len(tests) > 0, "no tests to run"

        runner = TestRunner(tests, machines, output, config.keep_alive)
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        output.print_exception(e)
        sys.exit(1)


if __name__ == "__main__":
    main()
