# 2404 dockerfile to install to debug auto-install issues
# see https://canonical-subiquity.readthedocs-hosted.com/en/latest/howto/autoinstall-validation.html#getting-started

FROM ubuntu:25.04

# Install dependencies
RUN apt-get update && \
    apt-get install -y build-essential git

# Add a fake sudo that just runs the command
RUN echo -e '#!/bin/sh\nexec "$@"' > /usr/local/bin/sudo && chmod +x /usr/local/bin/sudo

WORKDIR /tools
# setup tools
RUN git clone https://github.com/canonical/subiquity.git
WORKDIR /tools/subiquity/
RUN make install_deps
RUN ln -s /tools/subiquity/scripts/validate-autoinstall-user-data.py /usr/local/bin/validate-autoinstall-user-data
RUN ln -s /tools/subiquity/scripts/validate-autoinstall-user-data.py /usr/local/bin/validate-autoinstall-user-data.py

# Set the working directory
WORKDIR /tools/subiquity