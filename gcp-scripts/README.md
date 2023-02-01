# gcp-scripts

## killworker.sh

A script that can be used when troubleshooting GCP workers. When creating worker VMs manually through GCP, docker-worker will shut down the VM once it starts and realizes it lacks certain information and cannot proceed.
To combat this, one can use this script to kill the correct processes before it has a chance of shutting down the virtual machine.
