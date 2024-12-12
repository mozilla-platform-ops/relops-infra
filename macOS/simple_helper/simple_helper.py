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

def fetch_all_devices():
    """Fetch all devices using pagination."""
    endpoint = "devices"
    limit = 10  # Number of devices to fetch per page
    starting_after = None  # Used for pagination
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

def fetch_all_device_groups():
    """Fetch all device groups using pagination."""
    endpoint = "device_groups"
    limit = 10  # Number of groups to fetch per page
    starting_after = None  # Used for pagination
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

def list_devices():
    """List all devices using pagination."""
    all_devices = fetch_all_devices()
    for device in all_devices:
        print(f"ID: {device['id']}, Name: {device['attributes']['name']}")

def list_device_groups():
    """List all device groups using pagination."""
    all_groups = fetch_all_device_groups()
    for group in all_groups:
        print(f"ID: {group['id']}, Name: {group['attributes']['name']}")

def interactive_mode():
    """Interactive mode for the SimpleMDM tool."""
    print("Welcome to the SimpleMDM Tool Interactive Mode!")
    print("Type 'help' for a list of commands or 'exit' to quit.")

    commands = {
        "list-devices": list_devices,
        "list-device-groups": list_device_groups,
        "assign-device": lambda: assign_devices_to_group_with_picker(
            input("Enter hostnames (comma-separated): ").strip().split(",")
        ),
        "help": lambda: print("\n".join([
            "Commands:",
            "  list-devices                - List all devices",
            "  list-device-groups          - List all device groups",
            "  assign-device               - Assign multiple devices to a group using hostnames",
            "  exit                        - Exit the tool"
        ]))
    }

    try:
        while True:
            user_input = input("> ").strip()
            if user_input == "exit":
                print("Goodbye!")
                break
            elif user_input in commands:
                commands[user_input]()
            else:
                print("Unknown command. Type 'help' for a list of commands.")
    except KeyboardInterrupt:
        print("\nControl + C detected. Exiting...")

if __name__ == "__main__":
    try:
        interactive_mode()
    except KeyboardInterrupt:
        print("\nControl + C detected. Exiting...")