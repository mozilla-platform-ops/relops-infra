#!/usr/bin/env python3

import os
import sys
import argparse

def is_executable(file_path):
    """Check if a file is executable."""
    return os.path.isfile(file_path) and os.access(file_path, os.X_OK)

def create_symlink(source, destination):
    """Create a symbolic link."""
    try:
        os.symlink(source, destination)
        print(f"Symlink created: {source} -> {destination}")
    except FileExistsError:
        print(f"Symlink already exists: {destination}")
    except OSError as e:
        print(f"Error creating symlink: {e}")

def main(destination_dir, ignore_files):
    """Main function to create symlinks for executable scripts."""
    if not os.path.exists(destination_dir):
        print(f"Destination directory {destination_dir} does not exist.")
        sys.exit(1)

    if ignore_files:
        print(f"Ignoring files: {', '.join(ignore_files)}")
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    script_name = os.path.basename(__file__)

    for item in os.listdir(current_dir):
        item_path = os.path.join(current_dir, item)
        if item in ignore_files:
            print(f"Ignoring file: {item}")
        elif is_executable(item_path) and item != script_name:
            destination_path = os.path.join(destination_dir, item)
            create_symlink(item_path, destination_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create symlinks for executable scripts in the current directory.")
    parser.add_argument("destination_dir", type=str, help="The directory where the symlinks will be created")
    parser.add_argument("-i", "--ignore", action="append", default=[], help="Files to ignore (can be specified multiple times)")
    
    args = parser.parse_args()
    ignore_files = [os.path.basename(file) for file in args.ignore]
    main(args.destination_dir, ignore_files)