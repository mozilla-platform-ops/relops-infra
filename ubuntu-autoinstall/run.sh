#!/bin/zsh
# filepath: /Users/aerickson/git/relops-infra3/ubuntu-autoinstall/run.sh
# Run the image interactively, mounting the current directory as /etc/autoinstall for convenience
docker run --rm -it -v "$PWD":/etc/autoinstall ubuntu-autoinstall:latest /bin/bash