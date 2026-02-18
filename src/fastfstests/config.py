import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import tyro
from tyro.conf import OmitArgPrefixes, Positional, UseCounterAction, arg

logger = logging.getLogger(__name__)

CommaSeparatedList = Annotated[
    list[str],
    tyro.constructors.PrimitiveConstructorSpec(
        nargs=1,
        metavar="LIST",
        instance_from_str=lambda args: args[0].split(","),
        is_instance=lambda instance: isinstance(instance, list),  # pyright: ignore[reportAny]
        str_from_instance=lambda instance: [",".join(instance)],
    ),
]

SpaceSeparatedList = Annotated[
    list[str],
    tyro.constructors.PrimitiveConstructorSpec(
        nargs=1,
        metavar="LIST",
        instance_from_str=lambda args: args[0].split(" "),
        is_instance=lambda instance: isinstance(instance, list),  # pyright: ignore[reportAny]
        str_from_instance=lambda instance: [" ".join(instance)],
    ),
]


def hbh(hint: str) -> str:
    if hint == "''" or hint == "None" or hint == "":
        return ""
    return f"(default: {hint})"


@dataclass
class TestSelectionOptions:
    tests: Annotated[
        Positional[list[str]],
        arg(metavar="[TEST...]", help_behavior_hint=hbh),
    ] = field(default_factory=list)
    """space separated list of tests to run"""

    groups: Annotated[
        CommaSeparatedList,
        arg(aliases=["-g"], metavar="GROUP[,GROUP...]", help_behavior_hint=hbh),
    ] = field(default_factory=list)
    """comma separated list of groups to include tests from"""

    exclude_tests: Annotated[
        CommaSeparatedList,
        arg(aliases=["-e"], metavar="TEST[,TEST...]", help_behavior_hint=hbh),
    ] = field(default_factory=list)
    """comma separated list of tests to exclude"""

    exclude_tests_file: Annotated[
        Path | None,
        arg(aliases=["-E"], metavar="PATH", help_behavior_hint=hbh),
    ] = None
    """path to a file containing a line separated list of tests to exclude"""

    exclude_groups: Annotated[
        CommaSeparatedList,
        arg(aliases=["-x"], metavar="GROUP[,GROUP...]", help_behavior_hint=hbh),
    ] = field(default_factory=list)
    """comma separated list of groups to exclude tests from"""

    section: Annotated[
        str | None,
        arg(aliases=["-s"], metavar="SECTION", help_behavior_hint=hbh),
    ] = None
    """only include specific section"""

    exclude_section: Annotated[
        str | None,
        arg(aliases=["-S"], metavar="SECTION", help_behavior_hint=hbh),
    ] = None
    """exclude specific section"""

    randomize: Annotated[bool, arg(aliases=["-r"], help_behavior_hint=hbh)] = False
    """randomize test order"""

    iterate: Annotated[int, arg(aliases=["-i"], help_behavior_hint=hbh)] = 1
    """number of times to run each test"""

    list: Annotated[bool, arg(aliases=["-l"], help_behavior_hint=hbh)] = False
    """list tests to run, but don't actually run any tests"""

    file_system: Annotated[
        str | None, arg(metavar="FILESYSTEM", help_behavior_hint=hbh)
    ] = None
    """specify file system to be tested"""


@dataclass
class MkosiOptions:
    num: Annotated[int, arg(aliases=["-n"], help_behavior_hint=hbh)] = 10
    """number of mkosi vms to spawn"""

    config: Annotated[Path | None, arg(metavar="PATH", help_behavior_hint=hbh)] = None
    """mkosi config path (e.g. ~/mkosi-kernel/)"""

    options: Annotated[
        SpaceSeparatedList,
        arg(metavar="OPTION[ OPTION...]", help_behavior_hint=hbh),
    ] = field(default_factory=list)
    """list of options to pass through to mkosi (e.g. "--profile=fast-fstests --build-sources=~/kernel:kernel")"""

    include: Annotated[
        Path | None,
        arg(metavar="PATH", help_behavior_hint=hbh),
    ] = None
    """path to mkosi config to pass through to mkosi"""

    fstests: Annotated[Path | None, arg(metavar="PATH", help_behavior_hint=hbh)] = None
    """fstests dir path on mkosi vm"""

    timeout: Annotated[int, arg(metavar="SECONDS", help_behavior_hint=hbh)] = 30
    """max number of seconds to spawn a mkosi vm"""

    build: UseCounterAction[
        Annotated[int, arg(aliases=["-f"], help_behavior_hint=hbh)]
    ] = 0
    """build the image before spawning vms"""


@dataclass
class CustomVMOptions:
    vms: Annotated[
        CommaSeparatedList,
        arg(metavar="HOST:PATH[,HOST:PATH...]", help_behavior_hint=hbh),
    ] = field(default_factory=list)
    """
    comma separated list where each item is an ssh destination
    and a path to fstests separated by a colon (e.g. vm1:/fstests,vm2:/home/fstests)
    """


@dataclass
class OutputOptions:
    results_dir: Annotated[Path | None, arg(metavar="PATH", help_behavior_hint=hbh)] = (
        None
    )
    """path results directory"""

    verbose: Annotated[bool, arg(aliases=["-v"], help_behavior_hint=hbh)] = False
    """print debugging logs to RESULTS_DIR/log"""

    print_failure_list: Annotated[bool, arg(help_behavior_hint=hbh)] = False
    """print all failed tests in a pasteable way"""

    print_n_slowest: Annotated[int, arg(help_behavior_hint=hbh)] = 0
    """print n slowest tests"""

    print_duration_hist: Annotated[bool, arg(help_behavior_hint=hbh)] = False
    """print histogram of test times [plotext required]"""

    record: Annotated[bool, arg(help_behavior_hint=hbh)] = False
    """record this run as the baseline for future diffs"""

    diff: Annotated[bool, arg(help_behavior_hint=hbh)] = False
    """diff results against a recorded baseline"""

    def __post_init__(self):
        if self.verbose and self.results_dir is None:
            raise ValueError("--verbose requires --results-dir to be set")
        if self.record and self.results_dir is None:
            raise ValueError("--record requires --results-dir to be set")
        if self.diff and self.results_dir is None:
            raise ValueError("--diff requires --results-dir to be set")



@dataclass
class TestRunnerOptions:
    keep_alive: Annotated[bool, arg(help_behavior_hint=hbh)] = False
    """keep hosts alive for debugging purposes"""

    test_timeout: Annotated[
        int | None, arg(metavar="SECONDS", help_behavior_hint=hbh)
    ] = None
    """max number of seconds for an individual test"""

    bpftrace: Annotated[str | None, arg(metavar="BPFTRACE", help_behavior_hint=hbh)] = (
        None
    )
    """bpftrace script to be executed with -e"""

    bpftrace_script: Annotated[
        Path | None, arg(metavar="PATH", help_behavior_hint=hbh)
    ] = None
    """path to bpftrace script on vm"""

    probe_interval: Annotated[int, arg(metavar="SECONDS", help_behavior_hint=hbh)] = 30
    """seconds between liveness probes to check if hosts are still sshable (0 to disable)"""

    max_supervisor_restarts: Annotated[int, arg(metavar="N", help_behavior_hint=hbh)] = 3
    """max times a test can kill a supervisor before being marked as error (0 to disable restarts)"""

    dmesg: Annotated[bool, arg(help_behavior_hint=hbh)] = True
    """stream dmesg output during test execution"""


@dataclass
class Config:
    """
    fast-fstests is an fstests wrapper that parallelizes test execution with vms
    """

    fstests: Annotated[Path | None, arg(metavar="PATH", help_behavior_hint=hbh)] = (  # pyright: ignore[reportAny]
        tyro.MISSING
    )
    """path to fstests"""

    test_selection: OmitArgPrefixes[TestSelectionOptions] = field(
        default_factory=TestSelectionOptions
    )
    mkosi: MkosiOptions = field(default_factory=MkosiOptions)
    custom_vm: OmitArgPrefixes[CustomVMOptions] = field(default_factory=CustomVMOptions)
    output: OmitArgPrefixes[OutputOptions] = field(default_factory=OutputOptions)
    test_runner: OmitArgPrefixes[TestRunnerOptions] = field(
        default_factory=TestRunnerOptions
    )
