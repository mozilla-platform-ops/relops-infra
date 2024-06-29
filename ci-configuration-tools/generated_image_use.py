#!/usr/bin/env python3

import re
import argparse
from collections import Counter

# Initialize argument parser
parser = argparse.ArgumentParser(description='Process and count source images in a JSON file.')
parser.add_argument('-r', '--reverse', action='store_true', help='reverse the sorting order')
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

# Print the results
for source_image, count in sorted_counts:
    print(f'{count}: {source_image}')