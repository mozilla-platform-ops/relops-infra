# docker-worker test image scripts

see https://mozilla-hub.atlassian.net/wiki/spaces/ROPS/pages/156272519/Creating+GCP+Tester+VM+Images and https://mozilla-hub.atlassian.net/wiki/spaces/ROPS/pages/635076870/GCP+Tester+docker-worker+VM+Image+Notes


# test-instance.sh

v1 is the original script.
v2 is my initial modification shipped on the d-w testers.


## problems with v1 on instances with no ssd


```
+ lvdisplay
+ grep instance_storage
  No volume groups found
+ echo 'Creating logical volume '\''instance_storage'\'''
Creating logical volume 'instance_storage'
mount | grep " / " | awk '{print $1}'
++ mount
++ awk '{print $1}'
++ grep ' / '
+ root_device=/dev/sda1
+ '[' -e /dev/nvme0 ']'
+ '[' -e /dev/sdb ']'
+ root_device=/dev/sda1
ls -1 /dev/xvd* | grep -v "${root_device}"
++ ls -1 '/dev/xvd*'
ls: cannot access /dev/xvd*: No such file or directory
```

## v2's fix

v2 works on GCP instances with no local ssd 

## v3 proposed tweaks and notes

- Tweaks
  - The else block is unecessary.
- Notes
  - We don't want `elif [ -e /dev/sdb ]` to hit on GCP instances... they're not local ssd on this instance class. Good that it doesn't check for `/dev/sd*`.


## deployment

lives at:

```
/var/lib/cloud/scripts/per-boot/instance-storage.sh
```

on the image
