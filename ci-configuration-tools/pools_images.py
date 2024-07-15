#!/usr/bin/env python3

import yaml

# Function to load YAML file from disk
def load_yaml_file(file_path):
    with open(file_path, 'r') as file:
        return yaml.safe_load(file)

# Function to print pool names and images
def print_pool_names_and_images(data):
    for pool in data.get('pools', []):
        pool_name = pool.get('pool_id', 'N/A')
        image = pool.get('config', {}).get('image', 'N/A')
        # print(f"image FULL: {image}")
        if 'by-implementation' in image: 
           # display yaml block below
           # {'by-implementation': {'docker-worker': {'by-chain-of-trust': {'trusted': 'monopacker-docker-worker-trusted-current-gcp', 'default': 'monopacker-docker-worker-current'}}, 'generic-worker/.*': 'monopacker-ubuntu-2204-wayland'}}
          for key, value in image['by-implementation'].items():
              try:
                l1_img = value['by-chain-of-trust']['default']
                l3_img = value['by-chain-of-trust']['trusted']
                print(f"{pool_name} {key}: {l1_img}")
                print(f"{pool_name} L3: {l3_img}")
              except TypeError:
                print(f"{pool_name} {key}: {value}")
          # import sys
          # sys.exit(0)
        elif 'by-chain-of-trust' in image:
          l1_img = image['by-chain-of-trust']['default']
          l3_img = image['by-chain-of-trust']['trusted']
          print(f"{pool_name}: {l1_img}")
          print(f"{pool_name} L3: {l3_img}")
        else:
          print(f"{pool_name}: {image}")
        # print("cccc")



if __name__ == '__main__':
    # use argparse to show help
    import argparse

    parser = argparse.ArgumentParser(description='Reads worker-pools.yml and lists images used.')
    # parser.add_argument('-r', '--reverse', action='store_true', help='reverse the sorting order')
    args = parser.parse_args()
    

    # Path to the YAML file
    file_path = 'worker-pools.yml'

    # Load the YAML file
    yaml_data = load_yaml_file(file_path)

    # Print pool names and images
    print_pool_names_and_images(yaml_data)