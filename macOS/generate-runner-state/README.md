Certainly! Here’s the updated README.md with a section on how to create and use the binary using PyInstaller:

generate_runner_state.py

Overview

generate_runner_state.py is an interactive command-line utility for generating a runner_state.toml file based on specific machine configurations from Mozilla’s CI infrastructure. The utility clones the ronin_puppet repository, reads the appropriate YAML file for the selected machine group, and dynamically creates the TOML file with relevant configuration and state information.

The script offers an interactive menu for selecting group names, optional host limitation, and colorized output to enhance the user experience.

Features

	•	Interactive group selection: When running the script, a menu of valid group names is displayed for easy selection.
	•	Colorized output: The script provides colorized prompts and feedback for a more engaging terminal experience.
	•	Custom FQDN prefix:
	•	If the group name contains m1 or m2, the FQDN prefix is set to test.releng.mslv.mozilla.com.
	•	For other groups, the FQDN prefix is set to test.releng.mdc1.mozilla.com.
	•	Safe Runner directory: The generated runner_state.toml is written to a Safe Runner directory in the user’s home directory.
	•	Optional host limitation: The script allows you to limit the number of hosts that appear in the remaining_hosts field of the generated TOML file.
	•	Executable binary: The script can be compiled into a standalone binary using PyInstaller for easy distribution.

Requirements

	•	Python 3.x
	•	PyYAML library: Install with pip install pyyaml
	•	TOML library: Install with pip install toml
	•	colorama library: Install with pip install colorama for colorized terminal output
	•	Git must be installed to clone the repository.

Installation

	1.	Clone the repository:

git clone https://github.com/rcurranmoz/generate-runner-state.git


	2.	Make the Python script executable:

chmod +x generate_runner_state.py


	3.	Install the necessary Python dependencies:

pip install pyyaml toml colorama



Usage

Simply run the script and interact with the menu to choose a group and specify optional host limitations.

Example

./generate_runner_state.py

When you run the script, it will:

	•	Clone the ronin_puppet repository into /tmp (if not already cloned).
	•	Display a menu of valid group names to choose from.
	•	Optionally prompt you to limit the number of hosts.
	•	Create a runner_state.toml file in the ~/Safe Runner/ directory.

Sample Interaction:

Please choose a group from the following list:
1. gecko-t-osx-1015-r8
2. gecko-t-osx-1015-r8-staging
3. gecko-t-osx-1100-r8-latest
4. gecko-t-osx-1200-r8-latest
...

Enter the number of the group you want to select: 3
Enter the number of hosts to include (or press Enter to include all hosts): 3

Cloning repository https://github.com/mozilla-platform-ops/ronin_puppet.git into /tmp/ronin_puppet...
Repository already exists in /tmp/ronin_puppet, skipping clone...
runner_state.toml created successfully at /Users/yourusername/Safe Runner/runner_state.toml

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

If an invalid group name is selected, the script will exit with an error message.

Creating and Using a Binary

You can package this Python script into a standalone binary using PyInstaller. This is useful if you want to distribute the tool without requiring users to install Python or its dependencies.

Steps to Create the Binary

	1.	Install PyInstaller:

pip install pyinstaller


	2.	Create the binary:
Use PyInstaller to bundle the script into a single executable file:

pyinstaller --onefile generate_runner_state.py


	3.	Locate the binary:
After running the command, the binary will be created in the dist/ directory.
	4.	Run the binary:
Once the binary is created, you can run it without needing Python installed on the system:

./dist/generate_runner_state



Advantages of Using a Binary

	•	No need to install Python or any dependencies.
	•	Easier distribution for users who are not familiar with Python environments.
	•	The tool can be run like any other executable on your system.

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

The remaining_hosts are dynamically extracted from the specified YAML file based on the selected group name. If the --num_hosts option is provided, the number of hosts will be limited accordingly.

Contributing

Feel free to submit issues or pull requests if you have suggestions or improvements!

Changes Summary:

	•	Added a new Creating and Using a Binary section to guide users on how to package and run the script as a standalone binary using PyInstaller.
	•	Explained the advantages of using a binary for distribution.

Let me know if this is what you were looking for!