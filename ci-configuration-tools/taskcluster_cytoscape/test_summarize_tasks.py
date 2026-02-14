import pytest
from summarize_tasks import extract_group

@pytest.mark.parametrize("task_name,expected", [
    ("toolchain-macosx64-clang-14-raw", "toolchain*"),
    ("toolchain-linux64-gcc-10-opt", "toolchain*"),
    ("test-win64-xpcshell-2/dbg", "test-*-xpcshell*"),
    ("build-aarch64-opt", "build*"),
    ("l10n-macosx64-fr/opt", "l10n*"),
    ("simple-task", "simple*"),
    ("foo-bar", "foo*"),
])
def test_extract_group(task_name, expected):
    result = extract_group(task_name, debug=True)
    assert result == expected

@pytest.mark.parametrize("input_str,expected", [
    # control
    ("foo-*", "foo*"),
    ("foo-*-bar", "foo-*-bar"),
    # *-* tests
    ("foo-*-*-bar", "foo-*-bar"),
    ("foo-*-*-*-bar", "foo-*-bar"),
    ("*-*-foo", "*-foo"),
    ("**-foo", "*-foo"),
    ("foo-**", "foo*"),
    ("*-*-*", "*"),
    ## ** tests
    ("**", "*"),
    ("*-*-*-*", "*"),
    ("foo**bar", "foo*bar"),
    ("foo*-*bar", "foo*bar"),
    # -* tests
    ("build-*", "build*"),
])
def test_collapse_wildcards(input_str, expected):
    from summarize_tasks import collapse_wildcards
    assert collapse_wildcards(input_str) == expected
