import pytest
from summarize_tasks import extract_group

@pytest.mark.parametrize("task_name,expected", [
    ("toolchain-macosx64-clang-14-raw", "toolchain-*-clang-14"),
    ("toolchain-linux64-gcc-10-opt", "toolchain-*-*-10"),
    ("test-win64-xpcshell-2/dbg", "test-*-xpcshell-*"),
    ("build-aarch64-opt", "build-*-*"),
    ("l10n-macosx64-fr/opt", "l10n-*-*"),
    ("simple-task", "simple-task"),
    ("foo-bar", "foo-bar"),
])
def test_extract_group(task_name, expected):
    result = extract_group(task_name)
    assert result == expected
