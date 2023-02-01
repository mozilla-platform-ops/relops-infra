#!/bin/bash

# A very crude bash script to terminate docker-worker and worker-runner so they do not have a chance to shut down a GCP virtual machine
# in case we need to troubleshoot it.

while true; do ps aux | grep worker | grep -v kworker | awk -F ' ' '{print $2}' | xargs kill -9; sleep 0.1; done &
while true; do ps aux | grep runner | grep -v kworker | awk -F ' ' '{print $2}' | xargs kill -9; sleep 0.1; done &
