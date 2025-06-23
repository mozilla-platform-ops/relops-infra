import yaml
import re
from collections import defaultdict

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

def main():
    pools = load_yaml("worker-pools.yml")
    images = load_yaml("worker-images.yml")

    pool_to_image = defaultdict(list)
    for pool in pools.get("pools", []):
        pool_id = pool.get("pool_id")
        config = pool.get("config", {})
        image = config.get("image")
        if image:
            if isinstance(image, dict):
                for v in image.values():
                    if v:
                        pool_to_image[pool_id].append(v)
            else:
                pool_to_image[pool_id].append(image)

    lines = ["graph TD"]
    for pool_id, image_aliases in pool_to_image.items():
        pool_node = sanitize_node_id(pool_id.replace("-", "_").replace("/", "_"))
        lines.append(f'    {pool_node}["{pool_id}"]')
        for alias in image_aliases:
            if isinstance(alias, dict):
                # e.g., {'by-chain-of-trust': ...}
                for k, v in alias.items():
                    if v:
                        alias_str = f"{k}: {v}"
                        alias_node = sanitize_node_id(f"{pool_node}_{k}".replace("-", "_").replace("/", "_"))
                        lines.append(f'    {pool_node} --> {alias_node}["{alias_str}"]')
                        resolved = resolve_image_alias(v, images)
                        # handle resolved as before...
                        if isinstance(resolved, dict):
                            for provider, path in resolved.items():
                                path_str = f"{provider}: {path}" if isinstance(path, str) else f"{provider}: {path.get('name', '')}"
                                path_node = sanitize_node_id(f"{alias_node}_{provider}".replace("-", "_"))
                                lines.append(f'    {alias_node} --> {path_node}["{path_str}"]')
                        elif isinstance(resolved, str):
                            path_node = sanitize_node_id(f"{alias_node}_img")
                            lines.append(f'    {alias_node} --> {path_node}["{resolved}"]')
            else:
                alias_node = sanitize_node_id(alias.replace("-", "_").replace("/", "_"))
                lines.append(f'    {pool_node} --> {alias_node}["{alias}"]')
                resolved = resolve_image_alias(alias, images)
                if isinstance(resolved, dict):
                    for provider, path in resolved.items():
                        path_str = f"{provider}: {path}" if isinstance(path, str) else f"{provider}: {path.get('name', '')}"
                        path_node = sanitize_node_id(f"{alias_node}_{provider}".replace("-", "_"))
                        lines.append(f'    {alias_node} --> {path_node}["{path_str}"]')
                elif isinstance(resolved, str):
                    path_node = sanitize_node_id(f"{alias_node}_img")
                    lines.append(f'    {alias_node} --> {path_node}["{resolved}"]')

    with open("worker_pools_images.mmd", "w") as f:
        f.write("\n".join(lines))
    print("Mermaid diagram written to worker_pools_images.mmd")

if __name__ == "__main__":
    main()