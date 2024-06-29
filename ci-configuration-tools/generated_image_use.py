#!/usr/bin/env python3

import re
import argparse
import subprocess
from collections import Counter

# Initialize argument parser
parser = argparse.ArgumentParser(description='Process and count source images in a JSON file.')
parser.add_argument('-r', '--reverse', action='store_true', help='reverse the sorting order')
parser.add_argument('-a', '--aliases', action='store_true', help='call image_find_alias.py and display its output')
args = parser.parse_args()

# Initialize a counter for source images
source_image_counts = Counter()

# Regular expression to match the sourceImage line and extract the value
pattern = re.compile(r'"sourceImage":\s*"([^"]+)"')

# Read the JSON file line by line
with open('generated.json') as f:
    for line in f:
        match = pattern.search(line)
        if match:
            source_image = match.group(1)
            source_image_counts[source_image] += 1

# Sort and display the counts
sorted_counts = sorted(source_image_counts.items(), key=lambda x: x[1], reverse=args.reverse)

# Print the results and optionally run the command for each source image
for source_image, count in sorted_counts:
    search_value = source_image.split('/')[-1]
    
    if args.aliases:
        result = subprocess.run(['./image_find_alias.py', search_value], capture_output=True, text=True)
        alias_output = result.stdout.strip().replace('\n', ', ')
        print(f'{count}: {source_image} ({alias_output})')
    else:
        print(f'{count}: {source_image}')