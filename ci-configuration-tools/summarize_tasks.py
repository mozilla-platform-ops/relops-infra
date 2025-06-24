import re
from collections import Counter
import json
import sys
import argparse

# example: toolchain-macosx64-clang-14-raw
# result: toolchain-clang-14
#         or toolchain-clang?
def extract_group(task_name, debug=False):
    # remove /blah at end (usually opt or dbg)
    no_slash_task_name = re.sub(r"/[^/]+$", "", task_name)
    split_name = no_slash_task_name.split("-")

    # todo: eliminate some things from split_name... locales, platforms?
    temp_split_name = []
    drop_list = ['linux64', 'macosx64', 'win64', 'aarch64', 'x86_64', 'a55', 'macos', 'win32', 'linux2404', '1015', 'macosx1470', 'linux']
    release_drop_list = ['beta', 'nightly', 'release', 'esr', 'raw', 'opt', 'dbg']
    for item in split_name:
        if debug:
            print("* ", item, " ", len(item))

        if len(item) == 2 or len(item) == 3:
            # drop 2 letter items... usally locales
            temp_split_name.append("*")
        elif len(item) == 1:
            # drop shards...
            temp_split_name.append("*")
        elif item.startswith("fetch"):
            continue  # skip fetch tasks
        elif item in drop_list:
            # drop platform names
            temp_split_name.append("*")
        elif item in release_drop_list:
            # drop release names
            temp_split_name.append("*")
        else:
            # not dropped, so add to the list
            #
            if debug:
                print(f"added")
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
