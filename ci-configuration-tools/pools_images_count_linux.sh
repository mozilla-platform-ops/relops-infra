#!/usr/bin/env bash

set -e
#set -x

./pools_images.py | grep -v win | cut -f 2 -d ':' | sort | uniq -c | sort | grep -v win
