import os
import requests

API_KEY_ENV_VAR = "SIMPLEMDM_API_KEY"
BASE_URL = "https://a.simplemdm.com/api/v1/"

def get_api_key():
    """Fetch API key from environment variable."""
    api_key = os.getenv(API_KEY_ENV_VAR)
    if not api_key:
        raise ValueError(f"Environment variable {API_KEY_ENV_VAR} is not set.")
    return api_key

def make_request(endpoint, method="GET", payload=None):
    """Make a request to the SimpleMDM API."""
    api_key = get_api_key()
    url = BASE_URL + endpoint
    response = requests.request(method, url, auth=(api_key, ''), json=payload)
    if response.status_code in [200, 201]:
        return response.json()
    elif response.status_code == 202:  # Asynchronous success
        return None
    else:
        print(f"Error: {response.status_code} - {response.text}")
        return None

# Device and Group Management
def fetch_all_devices():
    """Fetch all devices using pagination."""
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
    """List all devices."""
    all_devices = fetch_all_devices()
    for device in all_devices:
        print(f"ID: {device['id']}, Name: {device['attributes']['name']}")

def fetch_all_device_groups():
    """Fetch all device groups using pagination."""
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
    """List all device groups."""
    all_groups = fetch_all_device_groups()
    for group in all_groups:
        print(f"ID: {group['id']}, Name: {group['attributes']['name']}")

def pick_device_group():
    """Display a picker to select a device group."""
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

def assign_devices_to_group_with_picker(hostnames):
    """Assign multiple devices to a group using a group picker."""
    all_devices = fetch_all_devices()

    group_id = pick_device_group()
    if not group_id:
        print("No group selected.")
        return

    successes = []
    failures = []

    for hostname in hostnames:
        print(f"\nProcessing hostname: {hostname}")
        device_id = None
        for device in all_devices:
            if device["attributes"]["name"] == hostname:
                device_id = device["id"]
                break

        if not device_id:
            print(f"Device with hostname '{hostname}' not found. Skipping...")
            failures.append(hostname)
            continue

        print(f"Assigning device {hostname} (ID: {device_id}) to group ID {group_id}...")
        response = requests.post(
            f"{BASE_URL}device_groups/{group_id}/devices/{device_id}",
            auth=(get_api_key(), '')
        )

        if response.status_code in [200, 201, 202]:
            print(f"Device {hostname} (ID: {device_id}) successfully assigned to group ID {group_id}.")
            successes.append(hostname)
        else:
            print(f"Failed to assign device {hostname} (ID: {device_id}) to group ID {group_id}.")
            print(f"Error: {response.status_code} - {response.text}")
            failures.append(hostname)

    print("\nSummary:")
    print(f"Successes: {', '.join(successes) if successes else 'None'}")
    print(f"Failures: {', '.join(failures) if failures else 'None'}")

# Script Management
def fetch_all_scripts():
    """Fetch all scripts using pagination."""
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
    """List all scripts."""
    all_scripts = fetch_all_scripts()
    for script in all_scripts:
        print(f"ID: {script['id']}, Name: {script['attributes']['name']}")

def pick_script():
    """Display a picker to select a script."""
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
    """Retrieve details of a specific script."""
    selected_script = pick_script()
    if not selected_script:
        print("No script selected.")
        return

    script_id = selected_script["id"]
    print(f"ID: {script_id}, Name: {selected_script['attributes']['name']}")
    print("Details:", selected_script["attributes"])

# Script Job Management
def create_script_job_with_picker(hostnames):
    """Create a script job for given hostnames using a script picker."""
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
    """Cancel a specific script job."""
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

# Interactive Mode
def interactive_mode():
    """Interactive mode for the Simple Helper tool."""
    print("Welcome to the Simple Helper Interactive Mode!")
    print("Type 'help' for a list of commands or 'exit' to quit.")

    commands = {
        "list-devices": list_devices,
        "list-device-groups": list_device_groups,
        "assign-device": lambda: assign_devices_to_group_with_picker(
            input("Enter hostnames (comma-separated): ").strip().split(",")
        ),
        "list-scripts": list_scripts,
        "retrieve-script": retrieve_script,
        "create-script-job": lambda: create_script_job_with_picker(
            input("Enter hostnames (comma-separated): ").strip().split(",")
        ),
        "cancel-script-job": cancel_script_job,
        "help": lambda: print("\n".join([
            "Commands:",
            "  list-devices                - List all devices",
            "  list-device-groups          - List all device groups",
            "  assign-device               - Assign multiple devices to a group using hostnames",
            "  list-scripts                - List all scripts",
            "  retrieve-script             - Retrieve details of a specific script",
            "  create-script-job           - Apply a script to specific hostnames",
            "  cancel-script-job           - Cancel a script job",
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

if __name__ == "__main__":
    try:
        interactive_mode()
    except Exception as e:
        print(f"An error occurred: {e}")