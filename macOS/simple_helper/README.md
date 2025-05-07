# 🛠️ Simple Helper Tool

An interactive CLI for managing **devices**, **device groups**, **scripts**, **script jobs**, and triggering **macOS updates** via the [SimpleMDM API](https://simplemdm.com/).

---

## 🚀 Features

- 📱 List & assign devices
- 🗂️ Manage device groups
- 📜 Run & cancel scripts
- 💻 Trigger macOS updates across multiple hosts
- 🔁 Interactive, resumable flow with CLI pickers

---

## 🧰 Requirements

- Python **3.7+** (Python 3.11+ recommended)
- [`pipenv`](https://pipenv.pypa.io/en/latest/) for environment management
- An environment variable `SIMPLEMDM_API_KEY` set with your SimpleMDM API key

---

## ⚙️ Setup

1. Clone the repository:

   ```bash
   git clone https://github.com/rcurranmoz/relops-infra.git
   cd simple_helper
   ```

2. Install dependencies:

   ```bash
   pipenv install
   ```

3. Activate the virtual environment:

   ```bash
   pipenv shell
   ```

4. (One-time) export your API key:

   ```bash
   export SIMPLEMDM_API_KEY=your_key_here
   ```

---

## 🧑‍💻 Usage

Run the tool:

```bash
python simple_helper.py
```

Type `help` in the prompt for a list of commands.

---

## 💡 Commands

### 📱 Device Management
- `list-devices` – List all enrolled devices
- `assign-device` – Assign one or more devices to a selected group

### 🗂️ Device Group Management
- `list-device-groups` – Show all device groups

### 📜 Script Management
- `list-scripts` – View all uploaded scripts
- `retrieve-script` – Show full details of a selected script

### 🧪 Script Jobs
- `create-script-job` – Push a selected script to specific hosts
- `cancel-script-job` – Cancel a selected script job

### 🔄 OS Update
- `update-os` – Trigger macOS updates for specified hostnames (e.g., `macmini-r8-1,macmini-r8-2`)

### 🆘 Help & Exit
- `help` – Show available commands
- `exit` – Exit the tool

---

## ⚠️ Notes

- Your API key must be set via the `SIMPLEMDM_API_KEY` environment variable before use.
- Use `pipenv shell` to activate the environment before each session.

---

## ❌ Exiting

- Type `exit` at any time, or press `Ctrl+C` to quit gracefully.

---
