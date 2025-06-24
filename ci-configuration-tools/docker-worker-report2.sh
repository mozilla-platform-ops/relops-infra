#!/usr/bin/env bash

set -e

./pools_images.py | grep docker-worker

./pools_images.py | grep docker-worker | wc -l