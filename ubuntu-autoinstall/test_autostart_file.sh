#!/bin/zsh
# filepath: /Users/aerickson/git/relops-infra3/ubuntu-autoinstall/run.sh
# Run the image interactively, mounting the current directory as /etc/autoinstall for convenience
# docker run --rm -it -v "$PWD":/etc/autoinstall ubuntu-autoinstall:latest /bin/bash

# check that an arg, the filename in the cwd to test is passed in
if [ -z "$1" ]; then
  echo "Usage: $0 <filename>"
  echo "  * The file should be in this directory."
  exit 1
fi

# TODO: instead of an interactive shell, run the /tools/subiquity/scripts/validate-autoinstall-user-data.py script on the input file
# to validate the autoinstall user data.
docker run --rm -v "$PWD":/etc/autoinstall ubuntu-autoinstall:latest /tools/subiquity/scripts/validate-autoinstall-user-data.py /etc/autoinstall/$@