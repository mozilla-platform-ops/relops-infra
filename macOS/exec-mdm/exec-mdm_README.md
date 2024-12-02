# exec-mdm

**`exec-mdm`** is a command-line tool for managing and executing scripts on devices using the [SimpleMDM API](https://simplemdm.com). It simplifies the process of searching for devices, selecting scripts, and running them across multiple devices efficiently.

## Features

- **Interactive Script Picker:** Browse available scripts using an intuitive arrow-key interface and select the desired one.
- **Device Lookup:** Search for devices by name and collect their IDs for batch operations.
- **Script Execution:** Execute scripts on multiple devices with a single command.
- **Pagination Support:** Handles large lists of scripts by automatically fetching all available entries from the API.

## Requirements

- Python 3.7+
- Python libraries:
  - `requests`
  - `inquirer`

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/mozilla-platform-ops/relops-infra.git
   cd macOS/exec-mdm/
   ```

2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

1. Run the tool:
   ```bash
   python exec-mdm.py
   ```

2. Enter your API key when prompted:
   ```
   Enter your API key: <YOUR_API_KEY>
   ```

3. Select a script:
   - The tool fetches all available scripts from SimpleMDM and displays them in an interactive picker. Use the arrow keys to navigate and press `Enter` to select.

4. Search for devices:
   - Enter device names one by one to collect their IDs. When finished, type `done` to proceed.

5. Confirm and execute:
   - The tool will run the selected script on the collected devices and provide feedback on the operation's success.

## Example Workflow

1. Start the tool and provide your API key:
   ```bash
   Enter your API key: abc123xyz
   ```

2. Select a script:
   ```
   Select the script to execute:
     ❯ Say hi
       Install Updates
       Reboot Devices
   ```

3. Look up devices:
   ```
   Enter the device name to look up (or type 'done' to finish): macmini-r8-100
   Device ID for 'macmini-r8-100': 477936
   Enter the device name to look up (or type 'done' to finish): done
   ```

4. Script job created successfully:
   ```
   Collected Device IDs: 477936
   Script job created successfully!
   Response: { ... }
   ```

## Contributing

Contributions are welcome! If you'd like to enhance **`exec-mdm`**, please follow these steps:

1. Fork the repository.
2. Create a new branch (`git checkout -b feature-name`).
3. Commit your changes (`git commit -am 'Add feature name'`).
4. Push to the branch (`git push origin feature-name`).
5. Create a pull request.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Support

If you encounter any issues or have questions, feel free to [open an issue](https://github.com/yourusername/exec-mdm/issues) or contact the repository owner.
