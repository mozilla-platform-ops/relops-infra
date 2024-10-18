generate_runner_state.py

Overview

generate_runner_state.py is a command-line utility for generating a runner_state.toml file based on specific machine configurations from Mozilla’s CI infrastructure. The utility clones the ronin_puppet repository, reads the appropriate YAML file for the selected machine group, and dynamically creates the TOML file with relevant configuration and state information.

Features

	•	Dynamic group selection: Pass a valid group name as a command-line argument to generate the TOML file.
	•	If the group name contains m1 or m2, the FQDN prefix is set to test.releng.mslv.mozilla.com.
	•	For other groups, the FQDN prefix is test.releng.mdc1.mozilla.com.
	•	Safe Runner directory: The generated runner_state.toml is written to a Safe Runner directory in the user’s home directory.
 	•	Optional host limitation: Use the --num_hosts flag to limit the number of hosts that appear in the remaining_hosts field of the generated TOML file.

Requirements

	•	Python 3.x
	•	PyYAML library: Install with pip install pyyaml
	•	TOML library: Install with pip install toml
	•	Git must be installed to clone the repository.

Installation

	1.	Clone the repository:

git clone https://github.com/rcurranmoz/generate-runner-state.git


	2.	Make the Python script executable:

chmod +x generate_runner_state.py


	3.	Install the necessary Python dependencies:

pip install pyyaml toml



Usage

Run the script by passing a valid group name as a command-line argument. For example:

./generate_runner_state.py <group_name>

Example

./generate_runner_state.py gecko-t-osx-1100-m1

This will:

	•	Clone the ronin_puppet repository into /tmp (if not already cloned).
	•	Read the relevant YAML file (macmini-m1.yaml for this example).
	•	Create a runner_state.toml file in the ~/Safe Runner/ directory.

Valid Group Names

The following group names are accepted by the script:

	•	gecko-t-osx-1015-r8
	•	gecko-t-osx-1015-r8-staging
	•	gecko-t-osx-1100-r8-latest
	•	gecko-t-osx-1200-r8-latest
	•	gecko-t-osx-1300-r8-latest
	•	gecko-t-osx-1400-r8-latest
	•	gecko-1-b-osx-1015
	•	gecko-3-b-osx-1015
	•	gecko-1-b-osx-1015-staging
	•	applicationservices-1-b-osx-1015
	•	applicationservices-3-b-osx-1015
	•	mozillavpn-b-1-osx
	•	mozillavpn-b-3-osx
	•	nss-1-b-osx-1015
	•	nss-3-b-osx-1015
	•	gecko-t-osx-1400-r8-staging
	•	gecko-t-osx-1400-m2-staging
	•	gecko-t-osx-1400-m2
	•	gecko-1-b-osx-arm64
	•	gecko-3-b-osx-arm64
	•	mozilla-b-1-osx
	•	mozilla-b-3-osx
	•	gecko-t-osx-1400-m2-vms-staging
	•	gecko-t-osx-1100-m1-staging
	•	gecko-t-osx-1100-m1

If an invalid group name is passed, the script will exit with an error message.

Output

The generated runner_state.toml file will contain the following structure:

	[config]
	command = "cd /tmp/ronin_puppet && bolt plan run deploy::apply_no_verify -t SR_HOST.test.releng.mdc1.mozilla.com noop=false -v --native-ssh"
	hosts_to_skip = []
	fqdn_prefix = "test.releng.mdc1.mozilla.com"
	provisioner = "releng-hardware"
	worker_type = "gecko-t-osx-1015-r8"
	
	[state]
	remaining_hosts = ["macmini-r8-1", "macmini-r8-2"]
	completed_hosts = []
	failed_hosts = []
	skipped_hosts = []

The remaining_hosts are dynamically extracted from the specified YAML file based on the provided group name.

Contributing

Feel free to submit issues or pull requests if you have suggestions or improvements!
