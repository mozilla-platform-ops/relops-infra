
# SimpleMDM Tool

This tool provides an interactive interface for managing devices and device groups using the SimpleMDM API.

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
python simple_mdm_tool.py
```

## Exiting

- Type `exit` to quit the tool.
- Press `Ctrl+C` to exit gracefully at any time.

## Notes

- Ensure your environment variable `SIMPLEMDM_API_KEY` is set before running the tool.
- Use `pipenv shell` to activate the environment before each session.
