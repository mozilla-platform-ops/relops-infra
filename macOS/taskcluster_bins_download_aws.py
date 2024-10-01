import requests
import os
import re
import boto3
from botocore.exceptions import NoCredentialsError

# GitHub API URL for latest releases
repo_owner = "taskcluster"
repo_name = "taskcluster"
url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/releases/latest"

# Directory to save the downloaded files
download_dir = "./downloads"
os.makedirs(download_dir, exist_ok=True)

# S3 bucket and path
s3_bucket = "ronin-puppet-package-repo"
s3_path = "macos/public/common/"

# Initialize S3 client
s3 = boto3.client('s3')

def download_file(url, save_path):
    response = requests.get(url, stream=True)
    with open(save_path, 'wb') as file:
        for chunk in response.iter_content(chunk_size=8192):
            file.write(chunk)

def upload_to_s3(file_path, s3_bucket, s3_key):
    try:
        s3.upload_file(file_path, s3_bucket, s3_key)
        print(f"Uploaded {file_path} to s3://{s3_bucket}/{s3_key}")
    except FileNotFoundError:
        print(f"File not found: {file_path}")
    except NoCredentialsError:
        print("Credentials not available")

# Fetch the latest release from GitHub API
response = requests.get(url)
if response.status_code == 200:
    release_data = response.json()
    version = release_data['tag_name'].lstrip('v')  # Strip 'v' from the version tag

    # Look for assets with 'darwin' in the name and exclude '.tar.gz' files
    for asset in release_data['assets']:
        asset_name = asset['name']
        if 'darwin' in asset_name and not asset_name.endswith('.tar.gz'):
            # Replace 'darwin' with the version number
            new_name = re.sub(r'darwin', version, asset_name)

            # Replace 'insecure' with 'simple' in the file name
            new_name = re.sub(r'insecure', 'simple', new_name)

            # Build full file paths
            download_url = asset['browser_download_url']
            save_path = os.path.join(download_dir, new_name)

            # Download and save the file
            print(f"Downloading {asset_name} as {new_name}")
            download_file(download_url, save_path)

            # Upload the downloaded file to S3
            s3_key = os.path.join(s3_path, new_name)
            upload_to_s3(save_path, s3_bucket, s3_key)

else:
    print(f"Failed to get release info: {response.status_code}")