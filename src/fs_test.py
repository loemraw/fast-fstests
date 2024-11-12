import re
import subprocess

import pytest


def __test(test, machine_id, mkosi_config_dir, fstests_dir):
    proc = subprocess.run(
        [
            "mkosi",
            "--machine",
            machine_id,
            "ssh",
            f"cd {fstests_dir} ; ./check {test}",
        ],
        cwd=mkosi_config_dir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    return proc.returncode, proc.stdout, proc.stderr


def summarize_stdout(test, stdout):
    start_token = test
    end_token = f"Ran: {test}"
    summary_pattern = re.compile(
        rf"{re.escape(start_token)}(.*?){re.escape(end_token)}",
        re.DOTALL,
    )
    match = summary_pattern.search(stdout)
    return f"{match.group(1).strip()}" if match else stdout


def summarize_stdout_skip(test, stdout):
    skip_token = "[not run]"
    reason_pattern = re.compile(rf"{re.escape(skip_token)}(.*)")
    match = reason_pattern.search(stdout)
    return f"{test}: {match.group(1).strip() if match else stdout}"


def test(test, machine_id, mkosi_config_dir, fstests_dir, record_test):
    status, stdout, stderr = __test(
        test, machine_id, mkosi_config_dir, fstests_dir
    )

    stdout = stdout.decode()
    stderr = stderr.decode()

    skip_token = "[not run]"
    if skip_token in stdout:
        summary = summarize_stdout_skip(test, stdout)
        record_test("skip", status, summary, stdout, stderr)
        pytest.skip(reason=summary)

    summary = summarize_stdout(test, stdout)
    if status != 0:
        record_test("fail", status, summary, stdout, stderr)
        pytest.fail(reason=summary, pytrace=False)

    record_test("pass", status, summary, stdout, stderr)
