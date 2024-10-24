import paramiko
import os

# Prompt for SSH details
hostname = input("Enter the hostname to SSH into: ")
username = input("Enter your SSH username: ")

# Specify the path to the SSH private key file (defaulting to ~/.ssh/id_ed25519)
ssh_key_path = input("Enter the path to your SSH private key file (or press enter to use '~/.ssh/id_ed25519'): ") or os.path.expanduser("~/.ssh/id_ed25519")

# Ensure the private key file exists
if not os.path.exists(ssh_key_path):
    print(f"SSH private key file not found at {ssh_key_path}")
    exit(1)

# Determine the key type and load the key
try:
    if ssh_key_path.endswith("rsa"):
        private_key = paramiko.RSAKey.from_private_key_file(ssh_key_path)
    elif ssh_key_path.endswith("ed25519"):
        private_key = paramiko.Ed25519Key.from_private_key_file(ssh_key_path)
    else:
        print("Unsupported key type. Please use RSA or Ed25519 keys.")
        exit(1)
except paramiko.SSHException as e:
    print(f"Failed to load the SSH key: {str(e)}")
    exit(1)

# Prompt for Puppet values
puppet_repo = input("Enter the puppet repo URL: ")
puppet_branch = input("Enter the puppet branch name: ")
puppet_mail = input("Enter the puppet mail address: ")

# Command to create the /etc/puppet/ronin_settings file remotely
command = f"""echo -e '# if you place this file at `/etc/puppet/ronin_settings`\n# the `run-puppet.sh` script will use the values here.\n\n# puppet overrides\nPUPPET_REPO={puppet_repo}\nPUPPET_BRANCH={puppet_branch}\nPUPPET_MAIL={puppet_mail}\n\n# taskcluster overrides\n# WORKER_TYPE_OVERRIDE=gecko-t-linux-talos-1804-staging' | sudo tee /etc/puppet/ronin_settings"""

# SSH to the remote machine and execute the command
try:
    # Create an SSH client
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # Connect to the remote machine using the SSH private key
    print(f"Connecting to {hostname} with the SSH key {ssh_key_path}...")
    client.connect(hostname, username=username, pkey=private_key)

    # Execute the command
    print("Generating the settings file on the remote machine...")
    stdin, stdout, stderr = client.exec_command(command)

    # Print the result of the command
    print(stdout.read().decode())
    print(stderr.read().decode())

    print(f"Settings file created successfully on {hostname}")
    
finally:
    # Close the SSH connection
    client.close()