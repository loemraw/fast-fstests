import subprocess

from parallelrunner.output import Output


def test_print_exception_string_arg():
    output = Output(results_dir=None)
    output.print_exception(ValueError("something went wrong"))


def test_print_exception_int_arg():
    output = Output(results_dir=None)
    exc = subprocess.CalledProcessError(1, ["mkosi", "build"])
    output.print_exception(exc)


def test_print_exception_empty_args():
    output = Output(results_dir=None)
    output.print_exception(ValueError())


def test_print_exception_with_notes():
    output = Output(results_dir=None)
    exc = RuntimeError("machine failed")
    exc.add_note("make sure to build your image first")
    exc.add_note("mkosi config path: /home/user/mkosi-kernel")
    output.print_exception(exc)


def test_print_exception_group():
    output = Output(results_dir=None)
    exc = ExceptionGroup("spawn failures", [
        ValueError("supervisor 1 failed"),
        RuntimeError("supervisor 2 timed out"),
    ])
    output.print_exception(exc)
