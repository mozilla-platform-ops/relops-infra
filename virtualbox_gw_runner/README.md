# virtualbox gw runner

Drives a Virtualbox VM as a Taskcluster worker.

## overview

what it does:
- stops the guest VM
- restores the VM to a snapshot
- optimizes guest CPU and memory to use max CPU and max RAM (minus a reserve)
- starts the guest VM
- configures /etc/hosts on guest vm to have GCP 'metadata' entry
- starts worker-manager inside the guest VM
- repeats the steps above forever


## installation

```bash
# clone repo
cd ~ubuntu
# TODO: get real repo link
git clone REPO .virtualbox_gw_runner

# setup poetry
poetry shell
poetry install

# place service file and reload systemd
sudo cp ~ubuntu/.virtualbox_gw_runner/virtualbox-gw-runner.service /etc/systemd/system/
sudo systemctl daemon-reload

# enable and start service
sudo systemctl start virtualbox-gw-runner.service
sudo systemctl enable virtualbox-gw-runner.service

# observe
systemctl status virtualbox-gw-runner
journalctl -u virtualbox-gw-runner --follow

# place TC component files
# - download and scp upload to versioned folder like 'v49.1.1' inside ~/.virtualbox_gw_runner
#
# $ ls v49.1.1
# checksums_v49.1.1.txt	start-worker*
# generic-worker-simple*	taskcluster-proxy*
# livelog*

# add secret to worker-runner-config.template
vi worker-runner-config.template
```
