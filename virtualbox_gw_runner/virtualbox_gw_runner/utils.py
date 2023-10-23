import os
import subprocess
import time

from virtualbox_gw_runner import exceptions


def touch(file_path):
    with open(file_path, "a"):
        os.utime(file_path, (time.time(), time.time()))


def run_command(
    command, print_output=True, raise_on_nonzero=True, test_mode=False, verbose=True
):
    if test_mode:
        command = "# " + command
    if verbose:
        print(f"  run_command: about to run: {command}")
    result = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True
    )

    output = result.stdout.decode("utf-8")
    return_code = result.returncode

    indentation = "\t"
    level = 2
    indented_text = indentation * level + output.replace(
        "\n", "\n" + indentation * level
    )
    if print_output and indented_text.strip():
        print(indented_text.rstrip())

    if verbose:
        print(f"  run_command: result code: {return_code}")

    if raise_on_nonzero and return_code != 0:
        raise exceptions.VBGWRNonZeroException

    # print(f"Output: {output}")
    # print(f"Return code: {return_code}")
    return (output, return_code)
