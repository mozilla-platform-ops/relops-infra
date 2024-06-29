#!/usr/bin/env python3

import yaml
import argparse

SUFFIXES_TO_STRIP = ['-fxci-level3-gcp', '-fxci-level1-gcp']

class WorkerImages:
    def __init__(self, file_path='worker-images.yml'):
        self.data = self.load_image_data(file_path)

    def load_image_data(self, file_path):
        with open(file_path, 'r') as file:
            return yaml.safe_load(file)

    def find_aliases(self, image_name):
        aliases = []
        for key, value in self.data.items():
            if image_name in key or (isinstance(value, str) and image_name in value):
                aliases.append(key)
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    if image_name in sub_value:
                        aliases.append(f"{key}-{sub_key}")
        # Strip off defined suffixes from the aliases
        aliases = [self.strip_suffix(alias) for alias in aliases]
        return aliases

    @staticmethod
    def strip_suffix(alias):
        for suffix in SUFFIXES_TO_STRIP:
            alias = alias.replace(suffix, '')
        return alias

def main():
    parser = argparse.ArgumentParser(description='Find aliases for a given image.')
    parser.add_argument('image_path', type=str, help='The image path to find aliases for')
    parser.add_argument('-o', '--one-line-each', action='store_true', help='Output each alias on a new line')
    
    args = parser.parse_args()
    worker_images = WorkerImages()
    aliases = worker_images.find_aliases(args.image_path)

    if aliases:
        if args.one_line_each:
            for alias in aliases:
                print(alias)
        else:
            print(', '.join(aliases))

if __name__ == "__main__":
    main()