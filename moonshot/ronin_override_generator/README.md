
# SSH Configuration Script for Puppet Settings

This Python script connects to a remote machine over SSH and generates a settings file (`/etc/puppet/ronin_settings`) with Puppet-specific configuration values. The script supports authentication using an RSA or Ed25519 private key from your local `.ssh` directory.

The script also prompts the user at the end if they would like to run `run-puppet.sh`. The output of both the settings file creation and the Puppet run is read incrementally to avoid blocking the script execution.

## Requirements

- Python 3.x
- **`paramiko`** library for SSH connections (see installation instructions below)

### Installing `paramiko`

The script requires the `paramiko` library for SSH functionality. If it's not installed, you can install it using `pip`:

\`\`\`bash
pip install paramiko
\`\`\`

## Usage

1. Clone or download the script to your local machine.
2. Run the script with Python 3:

\`\`\`bash
python3 linux_conf.py
\`\`\`

### Script Execution

The script will prompt for the following inputs:

1. **Hostname**: The hostname or IP address of the remote machine to SSH into.
2. **Username**: The SSH username for the remote machine.
3. **SSH Private Key**: The path to your SSH private key (default: `~/.ssh/id_ed25519`).
4. **Puppet Repo URL**: The URL for the Puppet repository.
5. **Puppet Branch Name**: The name of the Puppet branch.
6. **Puppet Mail Address**: The email address to associate with Puppet.

### Example

\`\`\`bash
Enter the hostname to SSH into: t-linux64-ms-137.test.releng.mdc1.mozilla.com
Enter your SSH username: myuser
Enter the path to your SSH private key file (or press enter to use '~/.ssh/id_ed25519'): 
Enter the puppet repo URL: 'https://github.com/myrepo/ronin_puppet.git'
Enter the puppet branch name: 'my-branch-name'
Enter the puppet mail address: 'myemail@example.com'
/etc/puppet/ronin_settings already exists. Do you want to overwrite it? (yes/no): yes
Generating the settings file on the remote machine...
Do you wish to run puppet now? (yes/no): yes
Running puppet on the remote machine...
\`\`\`

After providing the necessary inputs, the script will:

1. Connect to the remote machine using the SSH private key.
2. Create or overwrite the `/etc/puppet/ronin_settings` file on the remote machine with the provided Puppet configuration values.
3. Prompt the user whether to run Puppet via the `sudo /usr/local/bin/run-puppet.sh` script.

The output from both the settings file creation and Puppet run will be printed incrementally to avoid blocking the script, allowing the user to see live output.

## Notes

- Ensure the SSH public key (associated with the private key) is added to the remote machine's `~/.ssh/authorized_keys` file for the specified user.
- The script requires `sudo` privileges on the remote machine to write to `/etc/puppet/` and to run Puppet.
- The default SSH key is `~/.ssh/id_ed25519`. You can provide a custom key by specifying the file path when prompted.

## Troubleshooting

- If you encounter issues related to SSH authentication, verify that the SSH key is correctly configured on the remote machine.
- Ensure the hostname is reachable, and there are no network restrictions preventing the connection.
- If you get a "No existing session" error, ensure that you provided the correct SSH username and that the SSH key is compatible with the remote host.

## License

This project is licensed under the MIT License.
