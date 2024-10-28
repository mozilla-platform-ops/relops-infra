# pkg-scripts

These are scripts that we build pkgs from to automate provisioning before puppet takes over

__relops_key.sh__
+ Checks for the existance of either /Users/relops or /Users/administrator and proceeds to create a .ssh directory containing the relops public key

__sudoers__
+ Changes a line in sudoers to %admin          ALL=(ALL) NOPASSWD: ALL (required for Puppet)