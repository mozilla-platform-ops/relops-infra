#!/usr/bin/env bash

set -e
#set -x

./pool_images.py | cut -f 2 -d ':' | sort | uniq -c | sort
