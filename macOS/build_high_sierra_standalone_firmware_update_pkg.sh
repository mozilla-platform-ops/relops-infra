#!/bin/sh
# Based on investigations and work by Pepijn Bruienne
# Expects a single /Applications/Install macOS High Sierra*.app on disk

IDENTIFIER="org.mozilla.HighSierraFirmwareUpdateStandalone"
VERSION=1.0

# find the Install macOS High Sierra.app and mount the embedded InstallESD disk image
echo "Mounting High Sierra ESD disk image..."
/usr/bin/hdiutil mount /Applications/Install\ macOS\ High\ Sierra*.app/Contents/SharedSupport/InstallESD.dmg

# expand the FirmwareUpdate.pkg so we can copy resources from it
echo "Expanding FirmwareUpdate.pkg"
/usr/sbin/pkgutil --expand /Volumes/InstallESD/Packages/FirmwareUpdate.pkg /tmp/FirmwareUpdate

# we don't need the disk image any more
echo "Ejecting disk image..."
/usr/bin/hdiutil eject /Volumes/InstallESD

# make a place to stage our pkg resources
/bin/mkdir -p /tmp/HighSierraFirmwareUpdateStandalone/scripts

# write postinstall script
echo "Writing postinstall script"
cat <<EOF >> /tmp/HighSierraFirmwareUpdateStandalone/scripts/postinstall
#!/bin/sh

# run as root; firmware will be install on next boot
/usr/libexec/FirmwareUpdateLauncher -p "$PWD/Tools"
/usr/libexec/efiupdater -p "$PWD/Tools/EFIPayloads"

exit 0
EOF

# make sure postinstall is executable
echo "Setting postinstall to executable"
chmod 755 /tmp/HighSierraFirmwareUpdateStandalone/scripts/postinstall

# copy the needed resources
echo "Copying package resources..."
/bin/cp -R /tmp/FirmwareUpdate/Scripts/Tools /tmp/HighSierraFirmwareUpdateStandalone/scripts/

# build the package
echo "Building standalone package..."
/usr/bin/pkgbuild --nopayload --scripts /tmp/HighSierraFirmwareUpdateStandalone/scripts --identifier "$IDENTIFIER" --version "$VERSION" /tmp/HighSierraFirmwareUpdateStandalone.pkg

# clean up
/bin/rm -r /tmp/FirmwareUpdate
/bin/rm -r /tmp/HighSierraFirmwareUpdateStandalone

