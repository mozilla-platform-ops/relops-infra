#!/usr/bin/env python3
# filepath: /Users/aerickson/git/relops-infra/ci-configuration-tools/graph.py

import yaml
import re
from collections import defaultdict
import argparse
import sys
import os
import json
import logging
import platform
import socket
import getpass
import subprocess
from datetime import datetime

from summarize_tasks import extract_group

# Node type to color mapping (mirroring Mermaid)
NODE_COLORS = {
    "taskNode": "#f9f",        # light purple
    "poolNode": "#b6fcd5",     # light green
    "hwPoolNode": "#91c9aa",   # dark green
    "aliasNode": "#d0e7ff",    # light blue
    "imageNode": "#fff9b1",    # light yellow
    "l3imageNode": "#ffd6e0",  # light pink
}

def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)

def resolve_image_alias(image_alias, images):
    if not isinstance(image_alias, str):
        return image_alias
    if "{" in image_alias and "}" in image_alias:
        return image_alias
    seen = set()
    while isinstance(images.get(image_alias), str):
        if image_alias in seen:
            break
        seen.add(image_alias)
        image_alias = images[image_alias]
    return images.get(image_alias, image_alias)

def sanitize_node_id(s):
    return re.sub(r'[^a-zA-Z0-9_]', '', s)

def shorten_image_path(path):
    if not isinstance(path, str):
        return path
    return path.split('/')[-1]

def extract_image_aliases(image_config):
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
    parser = argparse.ArgumentParser(description="Generate Cytoscape JSON for worker pools and images.")
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

    elements = []
    node_ids = set()
    edge_ids = set()

    # Helper to add a node if not already present
    def add_node(node_id, label, node_type, extra_data=None):
        if node_id in node_ids:
            return
        node_ids.add(node_id)
        data = {
            "id": node_id,
            "label": label,
            "type": node_type,
            "color": NODE_COLORS.get(node_type, "#ccc"),
        }
        if extra_data:
            data.update(extra_data)
        elements.append({"data": data, "classes": node_type})

    # Helper to add an edge if not already present
    def add_edge(source, target, edge_type, extra_data=None):
        edge_id = f"{source}__{target}__{edge_type}"
        if edge_id in edge_ids:
            return
        edge_ids.add(edge_id)
        data = {
            "id": edge_id,
            "source": source,
            "target": target,
            "type": edge_type,
        }
        if extra_data:
            data.update(extra_data)
        elements.append({"data": data})

    # Pools, Aliases, and Images
    pool_to_image = defaultdict(list)
    pool_id_to_node = {}
    pool_id_patterns = []

    for pool in pools.get("pools", []):
        pool_id = pool.get("pool_id")
        if args.pool_exclude and args.pool_exclude in pool_id:
            continue
        pool_node = sanitize_node_id(pool_id.replace("-", "_").replace("/", "_"))
        pool_id_to_node[pool_id] = pool_node
        add_node(pool_node, pool_id, "poolNode", {"pool_id": pool_id, "config": pool.get("config", {})})
        if "{" in pool_id and "}" in pool_id:
            pattern = re.sub(r"\{[^}]+\}", r"[^/]+", pool_id)
            pool_id_patterns.append((re.compile(f"^{pattern}$"), pool_node))
        config = pool.get("config", {})
        image = config.get("image")
        if image:
            for alias in extract_image_aliases(image):
                if alias:
                    pool_to_image[pool_id].append(alias)

    # Aliases and resolved images
    for pool_id, image_aliases in pool_to_image.items():
        pool_node = pool_id_to_node[pool_id]
        for alias in image_aliases:
            alias_node = sanitize_node_id(alias.replace("-", "_").replace("/", "_"))
            add_node(alias_node, alias, "aliasNode", {"alias": alias})
            add_edge(pool_node, alias_node, "pool-alias")
            resolved = resolve_image_alias(alias, images)
            if isinstance(alias, str) and "{" in alias and "}" in alias:
                # Alias is an image, treat as imageNode
                elements[-2]["data"]["type"] = "imageNode"
                elements[-2]["data"]["color"] = NODE_COLORS["imageNode"]
                continue
            if isinstance(resolved, dict):
                for provider, path in resolved.items():
                    short_path = shorten_image_path(path) if isinstance(path, str) else path.get('name', '')
                    path_str = f"{provider}: {short_path}"
                    path_node = sanitize_node_id(f"{provider}_{short_path}".replace("-", "_"))
                    node_class = "l3imageNode" if "level3" in path_str else "imageNode"
                    add_node(path_node, path_str, node_class, {"provider": provider, "path": path})
                    add_edge(alias_node, path_node, "alias-image")
            elif isinstance(resolved, str):
                short_resolved = shorten_image_path(resolved)
                path_node = sanitize_node_id(short_resolved.replace("-", "_"))
                node_class = "l3imageNode" if "level3" in short_resolved else "imageNode"
                add_node(path_node, short_resolved, node_class, {"path": resolved})
                add_edge(alias_node, path_node, "alias-image")

    # Task Groups
    group_to_pools = defaultdict(set)
    group_counts = defaultdict(int)
    tasks_without_workertype_and_provisioner = 0

    for i, (task_label, task) in enumerate(tasks.items()):
        group = extract_group(task_label)
        group_counts[group] += 1
        prov = task.get("provisionerId") or (task.get("task", {}) or {}).get("provisionerId")
        wtype = task.get("workerType") or (task.get("task", {}) or {}).get("workerType")
        if not prov or not wtype:
            tasks_without_workertype_and_provisioner += 1
            continue
        pool_key = f"{prov}/{wtype}"
        pool_node = pool_id_to_node.get(pool_key)
        if not pool_node:
            for pattern, node in pool_id_patterns:
                if pattern.match(pool_key):
                    pool_node = node
                    break
        if pool_node:
            group_to_pools[group].add(pool_node)
        else:
            # Hardware pool
            hwpool_node = sanitize_node_id(f"hwpool_{pool_key.replace('/', '_')}")
            add_node(hwpool_node, pool_key, "hwPoolNode", {"pool_key": pool_key})
            group_to_pools[group].add(hwpool_node)

    # Add group nodes and edges
    for group, pool_nodes_set in group_to_pools.items():
        group_node = sanitize_node_id(group.replace("-", "_"))
        count = group_counts[group]
        add_node(group_node, f"{group} ({count})", "taskNode", {"group": group, "count": count})
        for pool_node in pool_nodes_set:
            add_edge(group_node, pool_node, "group-pool", {"group": group})

    # Add metadata
    def get_git_sha(path):
        try:
            sha = subprocess.check_output(
                ["git", "-C", path, "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
            ).decode().strip()
            dirty = subprocess.call(
                ["git", "-C", path, "diff-index", "--quiet", "HEAD", "--"]
            )
            if dirty != 0:
                sha += "-dirty"
            return sha
        except Exception:
            return None

    def get_git_remote(path):
        try:
            # Get current branch
            branch = subprocess.check_output(
                ["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD"], stderr=subprocess.DEVNULL
            ).decode().strip()
            # Get remote name for branch
            remote_name = subprocess.check_output(
                ["git", "-C", path, "config", f"branch.{branch}.remote"], stderr=subprocess.DEVNULL
            ).decode().strip()
            # Get remote URL
            remote_url = subprocess.check_output(
                ["git", "-C", path, "remote", "get-url", remote_name], stderr=subprocess.DEVNULL
            ).decode().strip()
            return {"branch": branch, "remote": remote_name, "url": remote_url}
        except Exception:
            return None

    def get_mtime(path):
        try:
            return datetime.fromtimestamp(os.path.getmtime(path)).isoformat()
        except Exception:
            return None

    metadata = {
        "created_at": datetime.now().isoformat(),
        "input_files": {
            "worker_pools_yml": {
                "path": pools_path,
                "mtime": get_mtime(pools_path),
            },
            "worker_images_yml": {
                "path": images_path,
                "mtime": get_mtime(images_path),
            },
            "tasks_json": {
                "path": tasks_path,
                "mtime": get_mtime(tasks_path),
            },
        },
        "fxci_git_sha": get_git_sha(os.path.expanduser(args.path_to_fxci_config)),
        "fxci_git_remote": get_git_remote(os.path.expanduser(args.path_to_fxci_config)),
        "mozilla_repo_git_sha": get_git_sha(os.path.expanduser(args.path_to_mozilla_repo)),
        "mozilla_repo_git_remote": get_git_remote(os.path.expanduser(args.path_to_mozilla_repo)),
        "task_count": len(tasks),
        "task_group_count": len(group_counts),
        "worker_pool_count": len([p for p in pools.get("pools", []) if not (args.pool_exclude and args.pool_exclude in p.get("pool_id", ""))]),
        "image_alias_count": len(set(alias for aliases in pool_to_image.values() for alias in aliases)),
        "python_version": platform.python_version(),
        "hostname": socket.gethostname(),
        "username": getpass.getuser(),
        "command_line": " ".join(sys.argv),
        "pool_exclude": args.pool_exclude,
        "script_version": get_git_sha(os.path.expanduser(args.path_to_fxci_config)),
    }

    # Show warnings
    if tasks_without_workertype_and_provisioner:
        print(f"Warning: {tasks_without_workertype_and_provisioner} tasks are missing workerType and provisionerId.")

    # Write output
    with open("worker_pools_images.cyto.json", "w") as f:
        json.dump({"elements": elements, "metadata": metadata}, f, indent=2)
    print("Cytoscape JSON written to worker_pools_images.cyto.json")

    # Show the metadata indented
    print(json.dumps(metadata, indent=2))

if __name__ == "__main__":
    main()