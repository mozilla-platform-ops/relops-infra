import os
import requests
import io

API_KEY_ENV_VAR = "SIMPLEMDM_API_KEY"
BASE_URL = "https://a.simplemdm.com/api/v1/"


# ------------------------------------------------------------
# Utility Functions
# ------------------------------------------------------------

def get_api_key():
    api_key = os.getenv(API_KEY_ENV_VAR)
    if not api_key:
        raise ValueError(f"Environment variable {API_KEY_ENV_VAR} is not set.")
    return api_key


def make_request(endpoint, method="GET", payload=None):
    api_key = get_api_key()
    url = BASE_URL + endpoint
    response = requests.request(method, url, auth=(api_key, ''), json=payload)
    if response.status_code in [200, 201]:
        return response.json()
    elif response.status_code == 202:
        return None
    else:
        print(f"Error: {response.status_code} - {response.text}")
        return None


# ------------------------------------------------------------
# Device Management
# ------------------------------------------------------------

def fetch_all_devices():
    endpoint = "devices"
    limit = 10
    starting_after = None
    all_devices = []

    print("Fetching all devices...")

    while True:
        paginated_endpoint = f"{endpoint}?limit={limit}"
        if starting_after:
            paginated_endpoint += f"&starting_after={starting_after}"

        response = make_request(paginated_endpoint)

        if response and "data" in response:
            devices = response["data"]
            all_devices.extend(devices)

            if devices:
                starting_after = devices[-1]["id"]
            else:
                break
        else:
            print("No more devices found or an error occurred.")
            break

    return all_devices


def list_devices():
    all_devices = fetch_all_devices()
    for device in all_devices:
        print(f"ID: {device['id']}, Name: {device['attributes']['name']}")


# ------------------------------------------------------------
# Device Group Management
# ------------------------------------------------------------

def fetch_all_device_groups():
    endpoint = "device_groups"
    limit = 10
    starting_after = None
    all_groups = []

    print("Fetching all device groups...")

    while True:
        paginated_endpoint = f"{endpoint}?limit={limit}"
        if starting_after:
            paginated_endpoint += f"&starting_after={starting_after}"

        response = make_request(paginated_endpoint)

        if response and "data" in response:
            groups = response["data"]
            all_groups.extend(groups)

            if groups:
                starting_after = groups[-1]["id"]
            else:
                break
        else:
            print("No more device groups found or an error occurred.")
            break

    return all_groups


def list_device_groups():
    all_groups = fetch_all_device_groups()
    for group in all_groups:
        print(f"ID: {group['id']}, Name: {group['attributes']['name']}")


def pick_device_group():
    all_groups = fetch_all_device_groups()

    if not all_groups:
        print("No device groups available.")
        return None

    print("\nAvailable Device Groups:")
    for i, group in enumerate(all_groups):
        print(f"{i + 1}. {group['attributes']['name']} (ID: {group['id']})")

    while True:
        try:
            choice = int(input("\nSelect a device group by number: ")) - 1
            if 0 <= choice < len(all_groups):
                return all_groups[choice]["id"]
            else:
                print("Invalid selection. Please try again.")
        except ValueError:
            print("Please enter a valid number.")


# ------------------------------------------------------------
# Script Management
# ------------------------------------------------------------

def fetch_all_scripts():
    endpoint = "scripts"
    limit = 10
    starting_after = None
    all_scripts = []

    print("Fetching all scripts...")

    while True:
        paginated_endpoint = f"{endpoint}?limit={limit}"
        if starting_after:
            paginated_endpoint += f"&starting_after={starting_after}"

        response = make_request(paginated_endpoint)

        if response and "data" in response:
            scripts = response["data"]
            all_scripts.extend(scripts)

            if scripts:
                starting_after = scripts[-1]["id"]
            else:
                break
        else:
            print("No more scripts found or an error occurred.")
            break

    return all_scripts


def list_scripts():
    all_scripts = fetch_all_scripts()
    for script in all_scripts:
        print(f"ID: {script['id']}, Name: {script['attributes']['name']}")


def pick_script():
    all_scripts = fetch_all_scripts()

    if not all_scripts:
        print("No scripts available.")
        return None

    print("\nAvailable Scripts:")
    for i, script in enumerate(all_scripts):
        print(f"{i + 1}. {script['attributes']['name']} (ID: {script['id']})")

    while True:
        try:
            choice = int(input("\nSelect a script by number: ")) - 1
            if 0 <= choice < len(all_scripts):
                return all_scripts[choice]
            else:
                print("Invalid selection. Please try again.")
        except ValueError:
            print("Please enter a valid number.")


def retrieve_script():
    selected_script = pick_script()
    if not selected_script:
        print("No script selected.")
        return

    script_id = selected_script["id"]
    print(f"ID: {script_id}, Name: {selected_script['attributes']['name']}")
    print("Details:", selected_script["attributes"])


# ------------------------------------------------------------
# Script Job Operations
# ------------------------------------------------------------

def create_script_job_with_picker(hostnames):
    selected_script = pick_script()
    if not selected_script:
        print("No script selected.")
        return

    script_id = selected_script["id"]

    all_devices = fetch_all_devices()
    device_ids = []

    for hostname in hostnames:
        for device in all_devices:
            if device["attributes"]["name"] == hostname:
                device_ids.append(device["id"])
                break
        else:
            print(f"Warning: Hostname '{hostname}' not found. Skipping...")

    if not device_ids:
        print("No valid devices found. Script job not created.")
        return

    payload = {
        "script_id": script_id,
        "device_ids": ",".join(map(str, device_ids))
    }
    response = requests.post(
        f"{BASE_URL}script_jobs",
        auth=(get_api_key(), ''),
        data=payload
    )

    if response.status_code in [200, 201]:
        print("Script job created successfully!")
    else:
        print(f"Failed to create script job. Error: {response.status_code} - {response.text}")


def cancel_script_job():
    endpoint = "script_jobs"
    all_jobs = make_request(endpoint)

    if not all_jobs or "data" not in all_jobs:
        print("No script jobs available.")
        return

    jobs = all_jobs["data"]

    print("\nAvailable Script Jobs:")
    for i, job in enumerate(jobs):
        print(f"{i + 1}. Job ID: {job['id']}")

    while True:
        try:
            choice = int(input("\nSelect a script job by number: ")) - 1
            if 0 <= choice < len(jobs):
                job_id = jobs[choice]["id"]
                response = requests.delete(
                    f"{BASE_URL}/script_jobs/{job_id}",
                    auth=(get_api_key(), '')
                )
                if response.status_code == 200:
                    print(f"Script job {job_id} canceled successfully.")
                else:
                    print(f"Failed to cancel script job {job_id}.")
                break
            else:
                print("Invalid selection. Please try again.")
        except ValueError:
            print("Please enter a valid number.")


# ------------------------------------------------------------
# NEW FEATURE: Apply ronin_settings via SimpleMDM Script Job
# ------------------------------------------------------------

def apply_ronin_settings_via_scriptjob():
    """
    Creates and runs a one-time SimpleMDM script job that writes the
    /opt/puppet_environments/ronin_settings file on all devices in a group.
    """

    group_id = pick_device_group()
    if not group_id:
        print("❌ No group selected.")
        return

    all_groups = fetch_all_device_groups()
    group_name = next(
        (g["attributes"]["name"] for g in all_groups if g["id"] == group_id),
        None
    )

    if not group_name:
        print("❌ Unable to resolve group name.")
        return

    repo = input("Enter PUPPET_REPO URL: ").strip()
    branch = input("Enter PUPPET_BRANCH name: ").strip()
    email = input("Enter PUPPET_MAIL: ").strip()

    ronin_content = f"""# if you place this file at `/opt/puppet_environments/ronin_settings`
# the `run-puppet.sh` script will use the values here.

# puppet overrides
PUPPET_REPO='{repo}'
PUPPET_BRANCH='{branch}'
PUPPET_MAIL='{email}'

# taskcluster overrides
# WORKER_TYPE_OVERRIDE='{group_name}'
"""

    # Bash script content
    script_body = (
        "#!/bin/bash\n"
        "set -e\n"
        "mkdir -p /opt/puppet_environments\n"
        "cat <<'EOF' > /opt/puppet_environments/ronin_settings\n"
        f"{ronin_content}"
        "EOF\n"
        "chown root:wheel /opt/puppet_environments/ronin_settings\n"
        "chmod 644 /opt/puppet_environments/ronin_settings\n"
        'echo \"✅ ronin_settings applied successfully\"\n'
    )

    api_key = get_api_key()

    print("\n📤 Uploading temporary script to SimpleMDM (multipart/form-data)...")
    files = {
        "file": ("ronin_settings.sh", io.BytesIO(script_body.encode("utf-8")), "text/plain")
    }
    data = {
        "name": f"Apply ronin_settings ({branch})",
        "variable_support": "0"
    }

    response = requests.post(
        f"{BASE_URL}scripts",
        auth=(api_key, ''),
        files=files,
        data=data
    )

    if response.status_code not in [200, 201]:
        print(f"❌ Failed to create script. Error: {response.status_code} - {response.text}")
        print("--- SCRIPT BODY SENT ---")
        print(script_body)
        print("------------------------")
        return

    script = response.json().get("data", {})
    script_id = script.get("id")
    print(f"✅ Created temporary script with ID {script_id}")

    print(f"📋 Fetching devices for group '{group_name}'...")
    all_devices = fetch_all_devices()
    device_ids = []
    for d in all_devices:
        group_rel = d.get("relationships", {}).get("device_group", {}).get("data", {})
        if str(group_rel.get("id")) == str(group_id):
            device_ids.append(str(d["id"]))

    if not device_ids:
        print(f"⚠️  No devices found in group '{group_name}'.")
        return

    print(f"🚀 Launching script job for {len(device_ids)} devices...")
    job_payload = {
        "script_id": script_id,
        "device_ids": ",".join(device_ids)
    }

    job_resp = requests.post(
        f"{BASE_URL}script_jobs",
        auth=(api_key, ''),
        data=job_payload
    )

    if job_resp.status_code in [200, 201, 202]:
        print("✅ Script job created successfully!")
    else:
        print(f"❌ Failed to create script job. Error: {job_resp.status_code} - {job_resp.text}")

    del_resp = requests.delete(f"{BASE_URL}scripts/{script_id}", auth=(api_key, ''))
    if del_resp.status_code == 200:
        print(f"🧹 Deleted temporary script {script_id}.")
    else:
        print(f"⚠️ Failed to delete script {script_id}: {del_resp.status_code}")


# ------------------------------------------------------------
# Interactive Mode
# ------------------------------------------------------------

def interactive_mode():
    print("Welcome to the Simple Helper Interactive Mode!")
    print("Type 'help' for a list of commands or 'exit' to quit.")

    commands = {
        "list-devices": list_devices,
        "list-device-groups": list_device_groups,
        "list-scripts": list_scripts,
        "retrieve-script": retrieve_script,
        "create-script-job": lambda: create_script_job_with_picker(
            input("Enter hostnames (comma-separated): ").strip().split(",")
        ),
        "cancel-script-job": cancel_script_job,
        "apply-ronin-scriptjob": apply_ronin_settings_via_scriptjob,
        "help": lambda: print("\n".join([
            "Commands:",
            "  list-devices                - List all devices",
            "  list-device-groups          - List all device groups",
            "  list-scripts                - List all scripts",
            "  retrieve-script             - Retrieve details of a specific script",
            "  create-script-job           - Apply a script to specific hostnames",
            "  cancel-script-job           - Cancel a script job",
            "  apply-ronin-scriptjob       - Create a one-time SimpleMDM script job to apply ronin_settings",
            "  exit                        - Exit the tool"
        ]))
    }

    while True:
        try:
            user_input = input("> ").strip()
            if user_input == "exit":
                print("Goodbye!")
                break
            elif user_input in commands:
                commands[user_input]()
            else:
                print("Unknown command. Type 'help' for a list of commands.")
        except KeyboardInterrupt:
            print("\nControl + C detected...exiting")
            break


# ------------------------------------------------------------
# Main Entry Point
# ------------------------------------------------------------

if __name__ == "__main__":
    try:
        interactive_mode()
    except Exception as e:
        print(f"An error occurred: {e}")