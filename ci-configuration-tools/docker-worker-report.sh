#!/usr/bin/env bash

set -e

./pools_images_count_linux.sh | grep docker-worker
