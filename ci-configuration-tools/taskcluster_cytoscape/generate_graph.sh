#!/usr/bin/env bash

set -e
set -x

./graph.py \
  -m ~/git/firefox \
  -p ~/git/fxci-config
