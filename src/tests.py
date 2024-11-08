import re
import subprocess

import pytest

pytest_plugins = ("xdist",)


def __test(test, machine_id, mkosi_kernel_dir, fstests_dir):
    proc = subprocess.run(
        [
            "mkosi",
            "--machine",
            machine_id,
            "ssh",
            f"cd {fstests_dir} ; ./check {test}",
        ],
        cwd=mkosi_kernel_dir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    return proc.returncode, proc.stdout, proc.stderr


def summarize_stdout_fail(test, stdout):
    start_token = test
    end_token = f"Ran: {test}"
    summary_pattern = re.compile(
        rf"{re.escape(start_token)}(.*?){re.escape(end_token)}",
        re.DOTALL,
    )
    match = summary_pattern.search(stdout)
    return f"\n{match.group(1).strip()}" if match else stdout


def summarize_stdout_skip(test, stdout):
    skip_token = "[not run]"
    reason_pattern = re.compile(rf"{re.escape(skip_token)}(.*)")
    match = reason_pattern.search(stdout)
    return f"{test}: {match.group(1).strip() if match else stdout}"


def test(test, machine_id, mkosi_kernel_dir, fstests_dir, record_test):
    status, stdout, stderr = __test(
        test, machine_id, mkosi_kernel_dir, fstests_dir
    )

    stdout = stdout.decode()
    stderr = stderr.decode()

    skip_token = "[not run]"
    if skip_token in stdout:
        record_test("skip", status, stdout, stderr)
        pytest.skip(reason=summarize_stdout_skip(test, stdout))

    if status != 0:
        record_test("fail", status, stdout, stderr)
        assert False, summarize_stdout_fail(test, stdout)

    record_test("pass", status, stdout, stderr)
