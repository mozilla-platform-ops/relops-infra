
# Simple Helper Tool

This tool provides an interactive interface for managing devices, device groups, scripts, and script jobs using the SimpleMDM API.

## Requirements

- Python 3.7 or higher
- Pipenv for dependency management

## Setup

1. Clone the repository or download the tool.
2. Open a terminal and navigate to the directory where the tool is located:
   ```bash
   cd /path/to/tool
   ```
3. Install the dependencies using Pipenv (one-time setup):
   ```bash
   pipenv install
   ```

4. Activate the Pipenv shell:
   ```bash
   pipenv shell
   ```

## Usage

Run the tool by executing:
```bash
python simple_helper.py
```

## Commands

Below are the available commands in the interactive mode:

1. **Device Management**:
   - `list-devices`: List all devices with pagination.
   - `assign-device`: Assign multiple devices to a device group using a picker.

2. **Device Group Management**:
   - `list-device-groups`: List all device groups with pagination.

3. **Script Management**:
   - `list-scripts`: List all available scripts.
   - `retrieve-script`: Retrieve details of a specific script using a picker.

4. **Script Job Management**:
   - `create-script-job`: Apply a script to specific hostnames using a picker.
   - `cancel-script-job`: Cancel a specific script job using a picker.

5. **Help**:
   - `help`: Display a list of available commands.

6. **Exit**:
   - `exit`: Quit the tool.

## Notes

- Ensure your environment variable `SIMPLEMDM_API_KEY` is set before running the tool.
- Use `pipenv shell` to activate the environment before each session.

## Exiting

- Type `exit` to quit the tool.
- Press `Ctrl+C` to exit gracefully at any time.
