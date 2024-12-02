import requests
import inquirer

# Prompt the user for API key
api_key = input("Enter your API key: ").strip()

# Fetch all scripts with pagination
scripts = {}
page = 1
limit = 100  # Set to the maximum allowed limit
while True:
    scripts_url = f"https://a.simplemdm.com/api/v1/scripts?limit={limit}&page={page}"
    response = requests.get(scripts_url, auth=(api_key, ""))
    
    if response.status_code == 200:
        scripts_data = response.json()
        if 'data' in scripts_data and scripts_data['data']:
            for script in scripts_data['data']:
                scripts[script['attributes']['name']] = script['id']
        else:
            break  # Exit if no more scripts are found
        
        # Check if there's more data to fetch
        if not scripts_data.get('has_more', False):
            break
        
        page += 1  # Move to the next page
    else:
        print(f"Failed to fetch scripts. Status code: {response.status_code}")
        print(response.text)
        exit()

if not scripts:
    print("No scripts found.")
    exit()

# Use inquirer to pick a script
questions = [
    inquirer.List(
        'script',
        message="Select the script to execute:",
        choices=list(scripts.keys()),
    )
]
selected_script_name = inquirer.prompt(questions)['script']
script_id = scripts[selected_script_name]
print(f"\nSelected Script: {selected_script_name} (ID: {script_id})")

# Initialize the list of device IDs
device_ids = []

# Allow user to look up device IDs by name
while True:
    search_term = input("\nEnter the device name to look up (or type 'done' to finish): ").strip()
    if search_term.lower() == 'done':
        break

    # Define the API endpoint for device lookup
    devices_url = f"https://a.simplemdm.com/api/v1/devices?search={search_term}"
    response = requests.get(devices_url, auth=(api_key, ""))
    
    if response.status_code == 200:
        data = response.json()
        if 'data' in data and data['data']:
            device_id = data['data'][0]['id']
            device_ids.append(device_id)
            print(f"Device ID for '{search_term}': {device_id}")
        else:
            print(f"No devices found for '{search_term}'.")
    else:
        print(f"Failed to fetch data for '{search_term}'. Status code: {response.status_code}")
        print(response.text)

# Display the list of collected device IDs
if device_ids:
    print("\nCollected Device IDs:", ",".join(map(str, device_ids)))
else:
    print("\nNo device IDs were collected.")
    exit()

# Define the endpoint for script jobs
script_jobs_url = "https://a.simplemdm.com/api/v1/script_jobs"

# Create the POST request payload
payload = {
    "script_id": script_id,
    "device_ids": ",".join(map(str, device_ids)),
}

# Make the POST request
response = requests.post(script_jobs_url, auth=(api_key, ""), data=payload)

# Handle the response
if response.status_code == 201:
    print("\nScript job created successfully!")
    print("Response:", response.json())
else:
    print("\nFailed to create script job.")
    print("Status code:", response.status_code)
    print(response.text)