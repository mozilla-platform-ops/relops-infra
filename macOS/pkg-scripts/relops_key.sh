#!/bin/zsh

# Define directories to check
USER_DIR=""
OWNER=""

if [ -d "/Users/relops" ]; then
    USER_DIR="/Users/relops"
    OWNER="relops:staff"
elif [ -d "/Users/administrator" ]; then
    USER_DIR="/Users/administrator"
    OWNER="administrator:staff"
else
    echo "Error: Neither /Users/relops/ nor /Users/administrator/ directory found."
    exit 1
fi

# Set .ssh and authorized_keys paths
SSH_DIR="$USER_DIR/.ssh"
AUTH_KEYS="$SSH_DIR/authorized_keys"
RSA_KEY="ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQDKQ5aTrL2DDI4LkvsXoR/sI94fPPExjRjLtt1QttaMUr6KETpBn0V43GqdKSzLNuv0iSbNYSCDwfe7TrNVxZ4oo7LpF6jAa0QOQVT1xCKNFITqkK/uR2ECJFVbZ/GP8oN+HrESvaRl0oglR/eGzxQcOiiZgAXUSasIe0FamVGPu46T4ski6A3bUTRG9Pjmy4m0KVjarIcphtybvyNpiicwDMicsJyDNbQw2tgZJNZgyewx7Y5OYSRJRTgHUxqrjUu3ZprmUleRMAP/Xez37Sc1GyE7m+hh2pK7lQSgIUd7bSwZW1iM8fr0KJ8CpFRi9jCFbI/WS6u3cF2ygeMQlX0/SGivn+AgHo5LoXzL2p7ib5zRJn7OuwFuCavuCPkJe1kISdQU+Xmm9vFCzXpiCx6dApuUcZdTGU+dnYzij9EqppHUDKonFV+Rq+Eqd43J8FMyIVIU6E/mEQh96dsKXADZhaji6i5T+E2v5Rd9AvGDcfYreVj2IzUF78CP9M5bhb7CZ/Nz+3GKc89mqqNBQrTFPLWA4NoECrlTGNiy83SZ8XcINiXdGFMEP8JBuKiPZAezHqWJgYdHa5p6zvzJDU8mbdkJ1kbpdy0WC/YGB9qkSaxIxk3s1jg1Rlm96jeP79OyGLPzO4CwOhFqQbOpcvmgwz+gG6oZwae0gX30DQoBxw== Relops rsa Key"

# Create the .ssh directory if it doesn't exist
if [ ! -d "$SSH_DIR" ]; then
    mkdir -p "$SSH_DIR"
    echo "Created $SSH_DIR."
fi

# Set correct ownership and permissions for the .ssh directory
chown "$OWNER" "$SSH_DIR"
chmod 700 "$SSH_DIR"
echo "Set ownership and permissions for $SSH_DIR."

# Create the authorized_keys file if it doesn't exist
if [ ! -f "$AUTH_KEYS" ]; then
    touch "$AUTH_KEYS"
    echo "Created $AUTH_KEYS."
fi

# Write the RSA key to authorized_keys
echo "$RSA_KEY" > "$AUTH_KEYS"

# Apply ownership and permissions to authorized_keys
chown "$OWNER" "$AUTH_KEYS"
chmod 600 "$AUTH_KEYS"
echo "Added RSA key and set permissions for $AUTH_KEYS."

echo "Setup complete."