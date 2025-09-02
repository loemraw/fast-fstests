import re

import pytest


def summarize_stdout(test, stdout):
    start_token = test
    end_token = f"Ran: {test}"
    summary_pattern = re.compile(
        rf"{re.escape(start_token)}(.*?){re.escape(end_token)}",
        re.DOTALL,
    )
    match = summary_pattern.search(stdout)
    return f"{match.group(1).strip()}" if match else stdout


def summarize_stdout_skip(stdout):
    skip_token = "[not run]"
    reason_pattern = re.compile(rf"{re.escape(skip_token)}(.*)")
    match = reason_pattern.search(stdout)
    return match.group(1).strip() if match else stdout


def test(test, run_test_):
    status, stdout, stderr = run_test_(test)

    stdout = stdout.decode()
    stderr = stderr.decode()

    skip_token = "[not run]"
    if skip_token in stdout:
        summary = summarize_stdout_skip(stdout)
        pytest.skip(reason=summary)

    summary = summarize_stdout(test, stdout)
    if status != 0:
        pytest.fail(reason=summary, pytrace=False)
