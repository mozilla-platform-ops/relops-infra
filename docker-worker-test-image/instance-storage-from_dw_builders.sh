#!/bin/bash -vxe

# The goal of this script is to initialize instance storage.
# We create an LVM logical volume from all available storage devices,
# format it, mount it, and ensure basic structure is in place.

# This is the URL for AWS. We don't support ram disk on other
# clouds for now
user_data_url=http://169.254.169.254/latest/user-data
curl_opt='--silent --connect-timeout 5'

# unless we are instructed to use tmpfs
if curl $curl_opt $user_data_url > /dev/null 2>&1; then
    tmpfs_size=$(curl $curl_opt $user_data_url | jq .tmpfsSize | tr -d \")
fi

# Create logical volume
# Do not attempt to create if volume already exists (upstart respawn).
if ! [ -z "$tmpfs_size" -o "$tmpfs_size" == "null" ]; then
    mount -t tmpfs -o size=$tmpfs_size tmpfs /mnt
elif ! lvdisplay | grep instance_storage; then
    echo "Creating logical volume 'instance_storage'"
    # Find instance storage devices
    # c5 and newer has nvme* devices. The nvmeN devices can't be used
    # with vgcreate. But nvmeNnN can.
    root_device=$(mount | grep " / " | awk '{print $1}')
    if [ -e /dev/nvme0 ]; then
        # root device is /dev/nvme[0-9]
        root_device=${root_device:0:10}
        devices=$(ls -1 /dev/nvme*n* | grep -v "${root_device}" || true)
    elif [ -e /dev/sda ]; then
        # root device is /dev/sd[a-z]
        root_device=${root_device:0:8}
        devices=$(ls /dev/sd* | grep -v "${root_device}" || true)
    elif [ -e /dev/xvda ]; then
        # root device is /dev/xvd[a-z]
        root_device=${root_device:0:9}
        devices=$(ls -1 /dev/xvd* | grep -v "${root_device}" || true)
    fi

    if [ -z "${devices}" ]; then
        echo "could not find devices to use for instance storage"
        # Bug 1570188; this is a hack to run in GCP where instance volumes aren't
        # configured.  We should do something much smarter.
        mkdir -p /mnt/var/lib/docker
        mkdir -p /mnt/docker-tmp
        mkdir -p /mnt/var/cache/docker-worker
        exit
    fi

    echo "Found devices: $devices"

    # Unmount block-device if already mounted, the first block-device always is
    for d in $devices; do umount $d || true; done

    # Create volume group containing all instance storage devices
    echo $devices | xargs vgcreate -y instance_storage

    # Create logical volume with all storage
    lvcreate -y -l 100%VG -n all instance_storage
else
    echo "Logical volume 'instance_storage' already exists"
fi

# Check to see if instance_storage-all is mounted already
if [ -z "$tmpfs_size" -o "$tmpfs_size" == "null" ]; then
    if ! df -T /dev/mapper/instance_storage-all | grep 'ext4'; then
        # Format logical volume with ext4
        echo "Logical volume does not appear mounted."
        echo "Formating 'instance_storage' as ext4"

        if ! mkfs.ext4 /dev/instance_storage/all; then
            echo "Could not format 'instance_storage' as ext4."
            exit 1
        else
            echo "Succesfully formated 'instance_storage' as ext4."
            echo "Mounting logical volume"

            # Our assumption is that workers are ephemeral. If errors are encountered, the
            # worker should be thrown away. Workers are never rebooted. So filesystem
            # durability isn't too important to us.

            # Default mount options: rw,relatime,errors=remount-ro,data=ordered
            #
            # We make the following changes:
            #
            # errors=panic -- The worker is unusable if the mount isn't writable. So
            # panic if we encounter this.
            #
            # data=writeback -- Don't require write ordering between journal and main
            # filesystem. Since we don't have a separate journal device, this probably
            # does little. But in theory it relaxes durability so it shouldn't hurt.
            #
            # nobarrier -- Loosen restrictions around writes to journal.
            #
            # commit=60 -- By default, ext4 tries to sync every 5s to ensure
            # minimal data loss in case of system failure. We increase that to 60s to
            # avoid excessive filesystem sync. The filesystem will still write out
            # changes in the background. And a sync() issued by an application can
            # still force a full flush sooner. But ext4 itself won't be flushing all
            # changes as often.
            mount -o 'rw,relatime,errors=panic,data=writeback,nobarrier,commit=60' /dev/instance_storage/all /mnt
        fi
    else
        echo "Logical volume 'instance_storage' is already mounted."
    fi
fi

echo "Creating docker specific directories"
mkdir -p /mnt/var/lib/docker
mkdir -p /mnt/docker-tmp
mkdir -p /mnt/var/cache/docker-worker