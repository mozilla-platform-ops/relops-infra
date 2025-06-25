import re
from collections import Counter
import json
import sys
import argparse

drop_list = ['linux64', 'macosx64', 'win64', 'aarch64', 'x86_64', 'a55', 'macos', 'win32', 'linux2404',
'1015', 'macosx1470', 'linux', 'ub20', 'ub18', 'ub24', 'macosx', 'windows11', 'linux2204']
release_drop_list = ['beta', 'nightly', 'release', 'esr', 'raw', 'opt', 'dbg']
two_or_three_char_drop_list = [
    "ach", "af", "afl", "all", "an", "apk", "apt", "ar", "arm", "as", "ast", "av", "az", "be", "bg", "bn", "bo", "br", "brx", "bs",
    "ca", "cak", "cft", "ckb", "cnn", "cs", "cy", "da", "de", "deb", "doc", "dsb", "el", "em", "en", "eo", "es", "et", "eu", "fa",
    "fat", "ff", "fi", "fix", "fr", "fur", "fy", "ga", "gcc", "gd", "gdb", "gl", "gn", "gu", "he", "hi", "hr", "hsb", "hu", "hw",
    "hy", "hye", "ia", "id", "ios", "is", "it", "ja", "jdk", "ka", "kab", "kk", "km", "kn", "ko", "lib", "lij", "lo", "lt", "ltg",
    "lv", "mac", "mar", "meh", "mk", "ml", "mr", "ms", "msi", "mwu", "my", "nb", "ndk", "ne", "nl", "nn", "oc", "osx", "pa", "pl",
    "pt", "rm", "ro", "rpm", "ru", "sat", "sc", "scn", "sco", "sdk", "si", "sk", "skr", "sl", "sm", "son", "sq", "sql", "sr", "sub",
    "sv", "szl", "ta", "tab", "te", "tg", "th", "tl", "to", "tps", "tr", "trs", "try", "tts", "ui", "uk", "ur", "uz", "vi", "w64",
    "win", "wo", "wpt", "x64", "x86", "xh", "zh",
    "CA", "GB", "AR", "CL", "ES", "MX", "NL", "IE", "AM", "NP", "BR", "PT", "SE", "CN", "TW"
]

# back to v1's ideas... or a variation?
#
# `test-win64-xpcshell-2/dbg` should become `test-*-xpcshell*`
#
# ideas:
#   - have a list of test types? (aka include list vs exclude list)
def extract_group(task_name, debug=False):
    # slashes are usually /opt, /dbg and we don't care about them.
    no_slash_task_name = re.sub(r"/[^/]+$", "", task_name)
    split_name = no_slash_task_name.split("-")

    result_str = ''
    max_elements = 1
    test_max_elements = 2
    element_counter = 0

    # trim temp_split_name to max_elements
    if task_name.startswith("test"):
        max_elements = test_max_elements
        # remove elements we don't want
        for item in split_name:
            # TODO: if we've reached max_elements, break
            if element_counter >= max_elements:
                break

            if item in drop_list or item in two_or_three_char_drop_list:
                result_str += "*-"
                # don't increment element_counter... these are not really elements
            else:
                result_str += item + "-"
                element_counter += 1
        result_str = result_str.strip("-")
        result_str += "*"
    else:
        # do trimming
        if len(split_name) > max_elements:
            split_name = split_name[:max_elements]
        result_str = "-".join(split_name).strip("-")
        # add the star
        result_str = result_str + "*"
    if debug:
        print(f"extract_group({task_name}) = {result_str}")
    return result_str

# a bit more complex
# - if test*, return 2 elements
# - else, return 1 element
def extract_group_v3(task_name, debug=False):
    no_slash_task_name = re.sub(r"/[^/]+$", "", task_name)
    split_name = no_slash_task_name.split("-")

    max_elements = 1
    test_max_elements = 2

    # trim temp_split_name to max_elements
    if task_name.startswith("test"):
        max_elements = test_max_elements

    # do trimming
    if len(split_name) > max_elements:
        split_name = split_name[:max_elements]
    result = "-".join(split_name).strip("-")
    return result

# basic, just returns first element
def extract_group_v2(task_name, debug=False):
    no_slash_task_name = re.sub(r"/[^/]+$", "", task_name)
    split_name = no_slash_task_name.split("-")
    max_elements = 1
    # trim temp_split_name to max_elements
    if len(split_name) > max_elements:
        split_name = split_name[:max_elements]
    result = "-".join(split_name).strip("-")
    return result

# example: toolchain-macosx64-clang-14-raw
# result: toolchain-clang-14
#         or toolchain-clang?
def extract_group_v1(task_name, debug=False):
    # remove /blah at end (usually opt or dbg)
    no_slash_task_name = re.sub(r"/[^/]+$", "", task_name)
    split_name = no_slash_task_name.split("-")

    # todo: eliminate some things from split_name... locales, platforms?
    temp_split_name = []

    for item in split_name:
        if debug:
            print("- ", item, " ", len(item))

        if item in two_or_three_char_drop_list or item.upper() in two_or_three_char_drop_list:
            # drop 2 letter items... usally locales
            temp_split_name.append("*")
            if debug:
                print(f"* added *")
        elif len(item) == 1:
            # drop shards...
            temp_split_name.append("*")
            if debug:
                print(f"* added *")
        elif item.startswith("fetch"):
            continue  # skip fetch tasks
        elif item in drop_list:
            # drop platform names
            temp_split_name.append("*")
            if debug:
                print(f"* added *")
        elif item in release_drop_list:
            # drop release names
            temp_split_name.append("*")
            if debug:
                print(f"* added *")
        else:
            # not dropped, so add to the list
            #
            if debug:
                print(f"* added")
            temp_split_name.append(item)

    max_elements = 4
    # trim temp_split_name to max_elements
    if len(temp_split_name) > max_elements:
        temp_split_name = temp_split_name[:max_elements]
    result = "-".join(temp_split_name).strip("-")

    # full string
    # result = "-".join(temp_split_name).strip("-")

    # print(f"input: {task_name}, result: {result}")
    return result

def load_json(path):
    with open(path) as f:
        return json.load(f)

def main():
    # run extract_group() on a heredoc of test strings
    test_strings = [
        "test-macosx-bar/2",
        "test-baz-qux/1222",
        "prod-foo-bar",
        "prod-baz-qux",
    ]

    for test_string in test_strings:
        extract_group(test_string, debug=True)

if __name__ == "__main__":
    main()
