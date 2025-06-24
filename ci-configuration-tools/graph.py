#!/usr/bin/env python3

import yaml
import re
from collections import defaultdict
import argparse
import subprocess
import sys
import os
import json
import logging
import pprint


from summarize_tasks import extract_group

# setup logging
# TODO: configure log level via argparse args
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)

def resolve_image_alias(image_alias, images):
    # Recursively resolve aliases to their final mapping
    if not isinstance(image_alias, str):
        return image_alias
    if "{" in image_alias and "}" in image_alias:
        # If alias contains curly brackets, treat as image, do not resolve further
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

def extract_image_aliases(image_config):
    """Recursively extract all image/alias strings from a nested image config."""
    if isinstance(image_config, str):
        return [image_config]
    elif isinstance(image_config, dict):
        results = []
        for v in image_config.values():
            results.extend(extract_image_aliases(v))
        return results
    elif isinstance(image_config, list):
        results = []
        for item in image_config:
            results.extend(extract_image_aliases(item))
        return results
    return []

def load_json(path):
    with open(path) as f:
        return json.load(f)

def main():
    parser = argparse.ArgumentParser(description="Generate Mermaid diagram for worker pools and images.")
    parser.add_argument('-g', '--generate', action='store_true', help='Generate image using mmdc')
    parser.add_argument('-e', '--pool-exclude', type=str, default=None, help='Exclude pools whose pool_id matches this string')
    parser.add_argument('-p', '--path-to-fxci-config', type=str, default='.', help='Path to look for the yaml files in (default: current directory)')
    parser.add_argument('-m', '--path-to-mozilla-repo', type=str, default='~/git/firefox', help='Path to look for the tasks files in (default: ~/git/firefox)')
    args = parser.parse_args()

    pools_path = os.path.join(os.path.expanduser(args.path_to_fxci_config), "worker-pools.yml")
    images_path = os.path.join(os.path.expanduser(args.path_to_fxci_config), "worker-images.yml")
    tasks_path = os.path.join(os.path.expanduser(args.path_to_mozilla_repo), "tasks.json")

    pools = load_yaml(pools_path)
    images = load_yaml(images_path)
    tasks = load_json(tasks_path)

    pool_to_image = defaultdict(list)
    for pool in pools.get("pools", []):
        pool_id = pool.get("pool_id")
        if args.pool_exclude and args.pool_exclude in pool_id:
            continue  # Skip excluded pools
        config = pool.get("config", {})
        image = config.get("image")
        if image:
            # Use the new recursive extractor
            for alias in extract_image_aliases(image):
                if alias:
                    pool_to_image[pool_id].append(alias)

    lines = [
        '%%{init: {  "maxTextSize": 99999999, "theme": "dark", "themeVariables": {}, "flowchart": { "htmlLabels": true, "curve": "curve", "useMaxWidth": 500, "diagramPadding": 10 } } }%%',
        "graph TD",
        "classDef taskNode fill:#f9f,stroke:#333,stroke-width:1px;",  # light purple
        "classDef poolNode fill:#b6fcd5,stroke:#333,stroke-width:1px;",  # light green
        "classDef aliasNode fill:#d0e7ff,stroke:#333,stroke-width:1px;",  # light blue
        "classDef imageNode fill:#fff9b1,stroke:#333,stroke-width:1px;",  # light yellow
        "classDef l3imageNode fill:#ffd6e0,stroke:#333,stroke-width:1px;",  # light pink
    ]
    pool_nodes = []
    alias_nodes = []
    image_nodes = []

    for pool_id, image_aliases in pool_to_image.items():
        pool_node = sanitize_node_id(pool_id.replace("-", "_").replace("/", "_"))
        lines.append(f'    {pool_node}["<pre>{pool_id}</pre>"]:::poolNode')
        pool_nodes.append(pool_node)
        for alias in image_aliases:
            alias_node = sanitize_node_id(alias.replace("-", "_").replace("/", "_"))
            lines.append(f'    {pool_node} --> {alias_node}["<pre>{alias}</pre>"]:::aliasNode')
            alias_nodes.append(alias_node)
            resolved = resolve_image_alias(alias, images)
            # If the alias is an image (contains curly brackets), make the alias node an imageNode
            if isinstance(alias, str) and "{" in alias and "}" in alias:
                # Change the class of the alias node to imageNode
                lines[-1] = f'    {pool_node} --> {alias_node}["<pre>{alias}</pre>"]:::imageNode'
                print("", f"Alias '{alias}' is an image, not resolving further.")
                # No further edges needed
                continue
            if isinstance(resolved, dict):
                for provider, path in resolved.items():
                    short_path = shorten_image_path(path) if isinstance(path, str) else path.get('name', '')
                    path_str = f"{provider}: {short_path}"
                    # Use provider and short_path for node ID to deduplicate
                    path_node = sanitize_node_id(f"{provider}_{short_path}".replace("-", "_"))
                    node_class = "l3imageNode" if "level3" in path_str else "imageNode"
                    lines.append(f'    {alias_node} --> {path_node}["<pre>{path_str}</pre>"]:::{node_class}')
                    image_nodes.append(path_node)
            elif isinstance(resolved, str):
                short_resolved = shorten_image_path(resolved)
                # Use short_resolved for node ID to deduplicate
                path_node = sanitize_node_id(short_resolved.replace("-", "_"))
                node_class = "l3imageNode" if "level3" in short_resolved else "imageNode"
                lines.append(f'    {alias_node} --> {path_node}["<pre>{short_resolved}</pre>"]:::{node_class}')
                image_nodes.append(path_node)

    # --- Add task nodes and edges ---
    # Build a mapping of pool_id to sanitized node id for quick lookup
    # pool_id_to_node = {}
    # pool_id_patterns = []
    # for pool in pools.get("pools", []):
    #     pool_id = pool.get("pool_id")
    #     pool_node = sanitize_node_id(pool_id.replace("-", "_").replace("/", "_"))
    #     pool_id_to_node[pool_id] = pool_node
    #     # If pool_id contains {var}, build a regex pattern for matching
    #     if "{" in pool_id and "}" in pool_id:
    #         # Replace {var} with [^/]+ (matches anything except '/')
    #         pattern = re.sub(r"\{[^}]+\}", r"[^/]+", pool_id)
    #         pool_id_patterns.append((re.compile(f"^{pattern}$"), pool_node))

    # unmatched_tasks = 0
    # max_tasks = 10  # Limit for performance; adjust as needed

    # for i, (task_label, task) in enumerate(tasks.items()):
    #     if i >= max_tasks:
    #         break  # Remove or adjust for full run
    #     task_node = sanitize_node_id(task_label.replace("-", "_"))
    #     lines.append(f'    {task_node}["<pre>{task_label}</pre>"]:::taskNode')
    #     # Get pool key from task
    #     prov = task.get("provisionerId") or (task.get("task", {}) or {}).get("provisionerId")
    #     wtype = task.get("workerType") or (task.get("task", {}) or {}).get("workerType")
    #     if not prov or not wtype:
    #         unmatched_tasks += 1
    #         continue
    #     pool_key = f"{prov}/{wtype}"
    #     # Try exact match
    #     pool_node = pool_id_to_node.get(pool_key)
    #     if not pool_node:
    #         # Try pattern match
    #         for pattern, node in pool_id_patterns:
    #             if pattern.match(pool_key):
    #                 pool_node = node
    #                 break
    #     if pool_node:
    #         lines.append(f'    {task_node} --> {pool_node}')
    #     else:
    #         unmatched_tasks += 1

    # if unmatched_tasks:
    #     print(f"Warning: {unmatched_tasks} tasks could not be matched to a pool node.")

    # --- Add summarized task group nodes and edges ---
    group_to_pools = defaultdict(set)
    pool_id_to_node = {}
    pool_id_patterns = []
    for pool in pools.get("pools", []):
        pool_id = pool.get("pool_id")
        pool_node = sanitize_node_id(pool_id.replace("-", "_").replace("/", "_"))
        pool_id_to_node[pool_id] = pool_node
        if "{" in pool_id and "}" in pool_id:
            pattern = re.sub(r"\{[^}]+\}", r"[^/]+", pool_id)
            pool_id_patterns.append((re.compile(f"^{pattern}$"), pool_node))

    unmatched_tasks = 0
    max_tasks = None  # No need to limit, since we're grouping

    for i, (task_label, task) in enumerate(tasks.items()):
        if max_tasks and i >= max_tasks:
            break  # Remove or adjust for full run
        # logging.debug(pprint.pformat(task))  # Debugging line to inspect task structure
        group = extract_group(task_label)
        prov = task.get("provisionerId") or (task.get("task", {}) or {}).get("provisionerId")
        wtype = task.get("workerType") or (task.get("task", {}) or {}).get("workerType")
        logging.debug(f"Processing task: {task_label}, group: {group}, provisioner: {prov}, workerType: {wtype}")  # Debugging line
        if not prov or not wtype:
            unmatched_tasks += 1
            continue
        pool_key = f"{prov}/{wtype}"
        pool_node = pool_id_to_node.get(pool_key)
        if not pool_node:
            # logging.debug(f"Pool key '{pool_key}' not found in pool_id_to_node, trying patterns...")  # Debugging line
            for pattern, node in pool_id_patterns:
                if pattern.match(pool_key):
                    pool_node = node
                    break
        if pool_node:
            logging.debug(f"Matched task '{task_label}' to pool node '{pool_node}'")
            group_to_pools[group].add(pool_node)
        else:
            logging.debug(f"No matching pool node found for task '{task_label}' with pool key '{pool_key}'")
            unmatched_tasks += 1

    # logging.debug(pprint.pformat(group_to_pools))  # Debugging line to inspect group_to_pools

    # print("exiting early!!!")
    # sys.exit(0)

    task_edge_count = 0
    for group, pool_nodes_set in group_to_pools.items():
        group_node = sanitize_node_id(group.replace("-", "_"))
        logging.debug(f"Creating group node for '{group}' with pool nodes: {pool_nodes_set}")  # Debugging line
        lines.append(f'    {group_node}["<pre>{group}</pre>"]:::taskNode')
        for pool_node in pool_nodes_set:
            lines.append(f'    {group_node}------->|{group_node}|{pool_node}')
            task_edge_count += 1
    logging.debug(f"Total task edges created: {task_edge_count}")  # Debugging line

    if unmatched_tasks:
        print(f"Warning: {unmatched_tasks} tasks could not be matched to a pool node.")

    with open("worker_pools_images.mmd", "w") as f:
        f.write("\n".join(lines))
    print("Mermaid diagram written to worker_pools_images.mmd")

    if args.generate:
        dest_name = "worker_pools_images.pdf"
        this_dir = os.path.dirname(os.path.realpath(__file__))
        mermaid_config_path = os.path.join(this_dir, "mermaid_config.json")

        try:
            subprocess.run([
                "mmdc",
                "-i", "worker_pools_images.mmd",
                "-o", dest_name,
                "-c", mermaid_config_path,
                # pdfFit trims the bottom of the doc too much, so we disable it
                "--pdfFit"  # Try to improve text rendering in PDF
            ], check=True)
            print(f"Image generated: {dest_name}")
            # print("If text is still not searchable, try generating SVG and converting to PDF with Inkscape or rsvg-convert.")
        except FileNotFoundError:
            print("Error: mmdc not found. Please install @mermaid-js/mermaid-cli.", file=sys.stderr)
        except subprocess.CalledProcessError as e:
            print(f"Error running mmdc: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()