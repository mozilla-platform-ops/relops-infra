#!/bin/sh
# Based on investigations and work by Pepijn Bruienne
# Expects a single /Applications/Install macOS Mojave*.app on disk

MACOS_NAME="MojaveBeta"
IDENTIFIER="org.mozilla.${MACOS_NAME}FirmwareUpdateStandalone"
VERSION=1.0

# find the Install macOS Mojave Beta.app and mount the embedded InstallESD disk image
echo "Mounting ${MACOS_NAME} ESD disk image..."
/usr/bin/hdiutil mount /Applications/Install\ macOS\ Mojave*.app/Contents/SharedSupport/InstallESD.dmg

# expand the FirmwareUpdate.pkg so we can copy resources from it
echo "Expanding FirmwareUpdate.pkg"
/usr/sbin/pkgutil --expand /Volumes/InstallESD/Packages/FirmwareUpdate.pkg /tmp/FirmwareUpdate

# we don't need the disk image any more
echo "Ejecting disk image..."
/usr/bin/hdiutil eject /Volumes/InstallESD

# make a place to stage our pkg resources
/bin/mkdir -p /tmp/${MACOS_NAME}FirmwareUpdateStandalone/scripts

# write postinstall script
echo "Writing postinstall script"
cat <<EOF >> /tmp/${MACOS_NAME}FirmwareUpdateStandalone/scripts/postinstall
#!/bin/sh

# run as root; firmware will be install on next boot
# make sure FirmwareUpdateLauncher exists; does not come installed on yosemite
/usr/libexec/FirmwareUpdateLauncher -p "$PWD/Tools"
/usr/libexec/efiupdater -p "$PWD/Tools/EFIPayloads"

exit 0
EOF

# make sure postinstall is executable
echo "Setting postinstall to executable"
chmod 755 /tmp/${MACOS_NAME}FirmwareUpdateStandalone/scripts/postinstall

# copy the needed resources
echo "Copying package resources..."
/bin/cp -R /tmp/FirmwareUpdate/Scripts/Tools /tmp/${MACOS_NAME}FirmwareUpdateStandalone/scripts/

# build the package
echo "Building standalone package..."
/usr/bin/pkgbuild --nopayload --scripts /tmp/${MACOS_NAME}FirmwareUpdateStandalone/scripts --identifier "$IDENTIFIER" --version "$VERSION" /tmp/${MACOS_NAME}FirmwareUpdateStandalone.pkg

# clean up
/bin/rm -r /tmp/FirmwareUpdate
/bin/rm -r /tmp/${MACOS_NAME}FirmwareUpdateStandalone

