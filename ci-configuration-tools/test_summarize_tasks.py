import pytest
from summarize_tasks import extract_group

@pytest.mark.parametrize("task_name,expected", [
    ("toolchain-macosx64-clang-14-raw", "toolchain-macosx64-clang"),
    ("toolchain-linux64-gcc-10-opt", "toolchain-linux64-gcc"),
    ("test-win64-xpcshell-2/dbg", "test-win64-xpcshell"),
    ("build-aarch64-opt", "build-aarch64"),
    ("l10n-macosx64-fr/opt", "l10n-macosx64"),
    ("simple-task", "simple-task"),
    ("foo-bar", "foo-bar"),
])
def test_extract_group(task_name, expected):
    result = extract_group(task_name)
    assert result.startswith(expected)
