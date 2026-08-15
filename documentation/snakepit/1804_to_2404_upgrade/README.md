# snakepit_upgrade_directions

## Overview

Steps for upgrading the cluster's worker nodes from Ubuntu 18.04 to 24.04.

## Things for aerickson to do before upgrading

1. TODO: Document networking (host ips, subnet mask, hostnames).
1. TODO: Document/save everything else in /etc for each host (nfs mounts, proxy configuration).
1. TODO: Add yarik to the hosts (so he can log in and execute shutdown)?
1. TODO: Finish this document.
1. TODO: Test this document on local VM.

## Preparation - in advance

1. Get a USB SSD or Flash Drive. 32GB should be plenty large (the image is ~3GB).
1. Procure crash cart device. IT recommends https://www.startech.com/en-us/server-management/notecons02.
1. Create a bootable USB installer for Ubuntu 24.04 Server edition.
  - Download the image from https://ubuntu.com/download/server.
  - Create bootable USB usning Balenda Etcher (https://etcher.balena.io/#download-etcher).

## Preparation - a week of before upgrades

1. Notify IT that we're going to to do this.

## Upgrade Directions

1. Make entry to room.
1. Connect crash cart device to a chassis. The head node (that we won't be upgrading this cycle) will be much larger than the worker nodes.
1. Verify that the host connected to is one of the hosts we'd like to upgrade.
  - TODO: double check this list
  - ok to upgrade: mlc0, mlc1, mlc2, mlc3, mlc4, mlc5
  - DO NOT UPGRADE mlchead
1. If the host is one we'd like to upgrade, insert the USB stick into the chassis.
1. Reboot the host and enter the BIOS/boot menu (typically by pressing F2, F12, or DEL during startup).
  - TODO: find out exact key combo for these
  - Log into the host and issue `shutdown -r now`.
1. Set the boot order to boot from the USB device first.
1. Save changes and exit the BIOS. The system should boot from the USB installer.
1. Select "Install Ubuntu Server" from the boot menu.
1. Choose language preferences and keyboard layout when prompted.
1. When prompted for network configuration, use the documented networking information from the preparation phase.
1. For the storage configuration:
  - TODO: do we want the existing layout or blast?
   - Select "Custom storage layout"
   - Preserve the existing partition scheme with root, swap, and any other partitions
   - Format the root partition but maintain the same mount points
1. Create the primary user account:
   - Username: admin (or use existing standard username)
   - TODO: it won't allow root?
   - TODO: put pw to use in 1p and share with yarik
1. When asked about SSH server installation, select "Install OpenSSH server".
1. Do not select additional packages when prompted to keep the installation minimal.
1. Allow the installation to complete. This may take 15-30 minutes.
1. When prompted to reboot, remove the USB stick and allow the system to restart.
1. After the system boots, log in with the credentials created during installation.
1. Set the hostname.
1. Set up networking.
1. Set up http proxy.
1. Setup NFS mounts.
  - add lines BLAH
  - `sudo mount -a`
  - `ls -la /blah`
1. Verify network connectivity to other nodes and the head node.
1. Add authorized key from mlchead to root's ~root/.ssh/authorized_keys file.
  - `mkdir -p ~root/.ssh`
  - `chmod 700 ~root/.ssh`
  - Add follwing key line to ~root/.ssh/authorized_keys: BLAH_TODO
  - `chmod 600 ~root/.ssh/authorized_keys`
1. Document the successful upgrade and any issues encountered.
1. Proceed to the next worker node and repeat the process.

