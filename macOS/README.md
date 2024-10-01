# macOS tools

__build_high_sierra_standalone_firmware_update_pkg.sh__
+ Extracts the firmware package from the High Sierra Installation App and creates a standalone pkg file to be installed on a host without having to run the installer
+ Downloading the High Sierra Installation App from the Apple App store or S3 is a prerequisite


__build_mojave_standalone_firmware_update_pkg.sh__
+ See notes above


__taskcluster_bins_download_aws.py__
+ Fetches the latest Taskcluster binaries, renames them and transfers them to the AWS bucket Puppet uses when converging macOS hosts