#!/usr/bin/env python3

import os
import yaml
import toml
import subprocess
import argparse
import sys

# List of acceptable group names
VALID_GROUP_NAMES = [
    "gecko-t-osx-1015-r8",
    "gecko-t-osx-1015-r8-staging",
    "gecko-t-osx-1100-r8-latest",
    "gecko-t-osx-1200-r8-latest",
    "gecko-t-osx-1300-r8-latest",
    "gecko-t-osx-1400-r8-latest",
    "gecko-1-b-osx-1015",
    "gecko-3-b-osx-1015",
    "gecko-1-b-osx-1015-staging",
    "applicationservices-1-b-osx-1015",
    "applicationservices-3-b-osx-1015",
    "mozillavpn-b-1-osx",
    "mozillavpn-b-3-osx",
    "nss-1-b-osx-1015",
    "nss-3-b-osx-1015",
    "gecko-t-osx-1400-r8-staging",
    "gecko-t-osx-1400-m2-staging",
    "gecko-t-osx-1400-m2",
    "gecko-1-b-osx-arm64",
    "gecko-3-b-osx-arm64",
    "mozilla-b-1-osx",
    "mozilla-b-3-osx",
    "gecko-t-osx-1400-m2-vms-staging",
    "gecko-t-osx-1100-m1-staging",
    "gecko-t-osx-1100-m1",
]

# Function to validate the group name
def validate_group_name(group_name):
    if group_name not in VALID_GROUP_NAMES:
        print(f"Error: '{group_name}' is not a valid group name.")
        sys.exit(1)

# Main function
def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Generate runner_state.toml for the given group.")
    parser.add_argument("group_name", help="The group name to use.")
    parser.add_argument(
        "--num_hosts", 
        type=int, 
        help="Optional: Specify the number of hosts to include in the remaining_hosts field."
    )
    args = parser.parse_args()

    # Validate the group name
    group_name = args.group_name
    validate_group_name(group_name)

    # Step 1: Define the repository URL and the target directory for cloning
    repo_url = "https://github.com/mozilla-platform-ops/ronin_puppet.git"
    clone_dir = "/tmp/ronin_puppet"

    # Step 2: Clone the repository into /tmp/ronin_puppet if it doesn't exist
    if not os.path.exists(clone_dir):
        print(f"Cloning repository {repo_url} into {clone_dir}...")
        subprocess.run(["git", "clone", repo_url, clone_dir], check=True)
    else:
        print(f"Repository already exists in {clone_dir}, skipping clone...")

    # Step 3: Determine the YAML file path based on the group_name
    if "m1" in group_name:
        yaml_file_path = os.path.join(clone_dir, "inventory.d", "macmini-m1.yaml")
    elif "m2" in group_name:
        yaml_file_path = os.path.join(clone_dir, "inventory.d", "macmini-m2.yaml")
    else:
        yaml_file_path = os.path.join(clone_dir, "inventory.d", "macmini-r8.yaml")

    # Step 4: Read the YAML file
    try:
        with open(yaml_file_path, 'r') as file:
            inventory_data = yaml.safe_load(file)
    except FileNotFoundError:
        print(f"Error: The file '{yaml_file_path}' does not exist.")
        sys.exit(1)

    # Step 5: Retrieve targets under the specified group name
    targets = []

    for group in inventory_data.get('groups', []):
        if group['name'] == group_name:
            targets = group.get('targets', [])
            break

    # Step 6: Only keep the part before the first dot for each target
    cleaned_targets = [target.split('.')[0] for target in targets]

    # Step 7: If num_hosts argument is provided, limit the number of hosts to that value
    if args.num_hosts and args.num_hosts <= len(cleaned_targets):
        cleaned_targets = cleaned_targets[:args.num_hosts]

    # Step 8: Get the current user using the environment variable
    current_user = os.environ['USER']

    # Step 9: Create the "Safe Runner" directory in the user's home directory if it doesn't exist
    safe_runner_dir = os.path.join(os.path.expanduser("~"), "Safe Runner")
    if not os.path.exists(safe_runner_dir):
        os.makedirs(safe_runner_dir)
        print(f"Created directory: {safe_runner_dir}")

    # Step 10: Check if the group_name contains "m1" or "m2" and set fqdn_prefix accordingly
    if "m1" in group_name or "m2" in group_name:
        fqdn_prefix = "test.releng.mslv.mozilla.com"
    else:
        fqdn_prefix = "test.releng.mdc1.mozilla.com"

    # Step 11: Construct the TOML data structure
    toml_data = {
        "config": {
            "command": f'cd {clone_dir} && bolt plan run deploy::apply_no_verify -t SR_HOST.{fqdn_prefix} noop=false -v --native-ssh',
            "hosts_to_skip": [],
            "fqdn_prefix": fqdn_prefix,  # fqdn_prefix dynamically set based on group_name
            "provisioner": "releng-hardware",
            "worker_type": group_name,  # group_name dynamically added to worker_type
        },
        "state": {
            "remaining_hosts": [],  # We will insert the cleaned targets as a string to avoid extra commas
            "completed_hosts": [],
            "failed_hosts": [],
            "skipped_hosts": [],
        }
    }

    # Step 12: Convert the TOML data (except remaining_hosts) to a string
    toml_string = toml.dumps(toml_data)

    # Step 13: Manually construct the remaining_hosts string without a trailing comma
    cleaned_targets_with_quotes = ['"{}"'.format(host) for host in cleaned_targets]
    remaining_hosts_str = f'remaining_hosts = [{", ".join(cleaned_targets_with_quotes)}]'

    # Step 14: Find where to insert remaining_hosts in the TOML string
    toml_string = toml_string.replace('remaining_hosts = []', remaining_hosts_str)

    # Step 15: Write the modified TOML string to the "Safe Runner" directory
    toml_file_path = os.path.join(safe_runner_dir, "runner_state.toml")
    with open(toml_file_path, 'w') as toml_file:
        toml_file.write(toml_string)

    print(f"'runner_state.toml' created successfully at {toml_file_path}")

# Run the main function
if __name__ == "__main__":
    main()