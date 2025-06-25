import re
from collections import Counter
import json
import sys
import argparse

# a bit more complex
# - if test*, return 2 elements
# - else, return 1 element
def extract_group(task_name, debug=False):
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
    drop_list = ['linux64', 'macosx64', 'win64', 'aarch64', 'x86_64', 'a55', 'macos', 'win32', 'linux2404',
    '1015', 'macosx1470', 'linux', 'ub20', 'ub18', 'ub24', 'macosx', 'windows11']
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
    parser = argparse.ArgumentParser()
    parser.add_argument('-t', '--test', dest='test_string', help='Test extract_group with a string')
    args = parser.parse_args()

    if args.test_string:
        print(extract_group(args.test_string, debug=True))
        return

    counter = Counter()
    with open("task_to_worker_type.txt") as f:
        for line in f:
            line = line.strip()
            if not line or not line.startswith("Task:"):
                continue
            task_name = line.split(",")[0].replace("Task: ", "")
            group = extract_group(task_name)
            counter[group] += 1

    print(f"{'Test Type':<20} {'Platform':<40} {'Count':>6}")
    print("="*70)
    for test_type, count in counter.most_common():
        print(f"{test_type:<20} {count:>6}")

if __name__ == "__main__":
    main()
