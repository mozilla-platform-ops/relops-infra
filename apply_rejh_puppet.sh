#!/usr/bin/env bash

# Example: ./apply_rejh_puppet.sh rejh{1,2}.srv.releng.{mdc1,mdc2}.mozilla.com

hosts=${@:-$(echo rejh{1,2}.srv.releng.{scl3,mdc1,mdc2}.mozilla.com)}

# 1. log into all first to open connections with mfa
# This expects ssh_config ControlPersist to be set (for long enough to keep
# the connection alive until the last one runs).

# 2. step through each with puppet

while read -u7 -r command; do
    echo $command
    for host in ${hosts}; do
        ssh "${host}" "${command}"
        echo $host
    done
done 7<<-"EOF"
    uptime
    sudo puppet agent --test
EOF
