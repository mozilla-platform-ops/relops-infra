#!/bin/zsh

# Backup sudoers file
cp /etc/sudoers /etc/sudoers.bak
echo "Backup of sudoers file created at /etc/sudoers.bak."

# Modify the sudoers file using sed to update the %admin line
sudo sed -i '' 's/%admin[[:space:]]*ALL = (ALL) ALL/%admin          ALL=(ALL) NOPASSWD: ALL/' /etc/sudoers

# Test the sudoers file to ensure there are no syntax errors
visudo -c
if [ $? -eq 0 ]; then
    echo "Sudoers file syntax is correct."
else
    echo "Error: Sudoers file has syntax errors. Reverting to backup."
    cp /etc/sudoers.bak /etc/sudoers
fi

echo "Sudoers modification complete."