#!/usr/bin/env python3

import yaml
import re
from collections import defaultdict
import argparse
import subprocess
import sys

def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)

def resolve_image_alias(image_alias, images):
    # Recursively resolve aliases to their final mapping
    if not isinstance(image_alias, str):
        return image_alias
    seen = set()
    while isinstance(images.get(image_alias), str):
        if image_alias in seen:
            break  # Prevent infinite loops
        seen.add(image_alias)
        image_alias = images[image_alias]
    return images.get(image_alias, image_alias)

def sanitize_node_id(s):
    # Only allow alphanumeric and underscores in node IDs
    return re.sub(r'[^a-zA-Z0-9_]', '', s)

def shorten_image_path(path):
    # Remove known prefixes from image paths
    if not isinstance(path, str):
        return path
    # Example: 'projects/taskcluster-imaging/global/images/docker-firefoxci-gcp-l1-googlecompute-2025-06-13t18-31-38z'
    # becomes 'docker-firefoxci-gcp-l1-googlecompute-2025-06-13t18-31-38z'
    return path.split('/')[-1]

def main():
    parser = argparse.ArgumentParser(description="Generate Mermaid diagram for worker pools and images.")
    parser.add_argument('-g', '--generate', action='store_true', help='Generate image using mmdc')
    parser.add_argument('--pool-exclude', type=str, default=None, help='Exclude pools whose pool_id matches this string')
    args = parser.parse_args()

    pools = load_yaml("worker-pools.yml")
    images = load_yaml("worker-images.yml")

    pool_to_image = defaultdict(list)
    for pool in pools.get("pools", []):
        pool_id = pool.get("pool_id")
        if args.pool_exclude and args.pool_exclude in pool_id:
            continue  # Skip excluded pools
        config = pool.get("config", {})
        image = config.get("image")
        if image:
            if isinstance(image, dict):
                for v in image.values():
                    if v:
                        pool_to_image[pool_id].append(v)
            else:
                pool_to_image[pool_id].append(image)

    lines = [
        "graph TD",
        "classDef poolNode fill:#b6fcd5,stroke:#333,stroke-width:1px;",  # light green
        "classDef aliasNode fill:#d0e7ff,stroke:#333,stroke-width:1px,width:600px;",  # light blue
        "classDef imageNode fill:#fff9b1,stroke:#333,stroke-width:1px;",  # light yellow
        "classDef l3imageNode fill:#ffd6e0,stroke:#333,stroke-width:1px;",  # light pink
    ]
    pool_nodes = []
    alias_nodes = []
    image_nodes = []

    for pool_id, image_aliases in pool_to_image.items():
        pool_node = sanitize_node_id(pool_id.replace("-", "_").replace("/", "_"))
        lines.append(f'    {pool_node}["{pool_id}"]:::poolNode')
        pool_nodes.append(pool_node)
        for alias in image_aliases:
            if isinstance(alias, dict):
                # e.g., {'by-chain-of-trust': ...}
                for k, v in alias.items():
                    if v:
                        alias_str = f"{k}: {v}"
                        alias_node = sanitize_node_id(f"{pool_node}_{k}".replace("-", "_").replace("/", "_"))
                        lines.append(f'    {pool_node} --> {alias_node}["{alias_str}"]:::aliasNode')
                        alias_nodes.append(alias_node)
                        resolved = resolve_image_alias(v, images)
                        if isinstance(resolved, dict):
                            for provider, path in resolved.items():
                                # Shorten path for display
                                short_path = shorten_image_path(path) if isinstance(path, str) else path.get('name', '')
                                path_str = f"{provider}: {short_path}"
                                path_node = sanitize_node_id(f"{alias_node}_{provider}".replace("-", "_"))
                                # Check for 'level3' in path_str to determine node class
                                node_class = "l3imageNode" if "level3" in path_str else "imageNode"
                                lines.append(f'    {alias_node} --> {path_node}["{path_str}"]:::{node_class}')
                                image_nodes.append(path_node)
                        elif isinstance(resolved, str):
                            short_resolved = shorten_image_path(resolved)
                            path_node = sanitize_node_id(f"{alias_node}_img")
                            # Check for 'level3' in short_resolved to determine node class
                            node_class = "l3imageNode" if "level3" in short_resolved else "imageNode"
                            lines.append(f'    {alias_node} --> {path_node}["{short_resolved}"]:::{node_class}')
                            image_nodes.append(path_node)
            else:
                alias_node = sanitize_node_id(alias.replace("-", "_").replace("/", "_"))
                lines.append(f'    {pool_node} --> {alias_node}["{alias}"]:::aliasNode')
                alias_nodes.append(alias_node)
                resolved = resolve_image_alias(alias, images)
                if isinstance(resolved, dict):
                    for provider, path in resolved.items():
                        short_path = shorten_image_path(path) if isinstance(path, str) else path.get('name', '')
                        path_str = f"{provider}: {short_path}"
                        path_node = sanitize_node_id(f"{alias_node}_{provider}".replace("-", "_"))
                        # Check for 'level3' in path_str to determine node class
                        node_class = "l3imageNode" if "level3" in path_str else "imageNode"
                        lines.append(f'    {alias_node} --> {path_node}["{path_str}"]:::{node_class}')
                        image_nodes.append(path_node)
                elif isinstance(resolved, str):
                    short_resolved = shorten_image_path(resolved)
                    path_node = sanitize_node_id(f"{alias_node}_img")
                    # Check for 'level3' in short_resolved to determine node class
                    node_class = "l3imageNode" if "level3" in short_resolved else "imageNode"
                    lines.append(f'    {alias_node} --> {path_node}["{short_resolved}"]:::{node_class}')
                    image_nodes.append(path_node)

    with open("worker_pools_images.mmd", "w") as f:
        f.write("\n".join(lines))
    print("Mermaid diagram written to worker_pools_images.mmd")

    if args.generate:
        dest_name = "worker_pools_images.pdf"
        try:
            subprocess.run([
                "mmdc",
                "-i", "worker_pools_images.mmd",
                "-o", dest_name
            ], check=True)
            print(f"Image generated: {dest_name}")
        except FileNotFoundError:
            print("Error: mmdc not found. Please install @mermaid-js/mermaid-cli.", file=sys.stderr)
        except subprocess.CalledProcessError as e:
            print(f"Error running mmdc: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()