# docker-worker test image scripts

see https://mozilla-hub.atlassian.net/wiki/spaces/ROPS/pages/156272519/Creating+GCP+Tester+VM+Images and https://mozilla-hub.atlassian.net/wiki/spaces/ROPS/pages/635076870/GCP+Tester+docker-worker+VM+Image+Notes

v1 is the original script. the unversioned script is my modification.

# test-instance.sh

lives at:

```
/var/lib/cloud/scripts/per-boot/instance-storage.sh
```

on the image