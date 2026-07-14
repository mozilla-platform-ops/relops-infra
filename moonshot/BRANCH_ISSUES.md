# PR 36 Branch Review

Review of [mozilla-platform-ops/relops-infra#36](https://github.com/mozilla-platform-ops/relops-infra/pull/36), "ci config tool work 062325 (includes taskcluster cytoscape viewer)."

Reviewed on 2026-07-14. The checked-out branch, `ci-config-tool-work-062325`, matches the PR head at commit `a07582c54798aa2f2fca466c9e0cb6708c79de72`.

## Summary

The Cytoscape viewer is a useful reporting prototype, but its pool-resolution logic can produce incorrect task-to-pool relationships. The two highest-priority issues are:

1. Overlapping worker-pool templates are resolved by YAML order rather than specificity.
2. Pools selected by `--pool-exclude` can return as incorrectly classified hardware pools.

Both problems affect the accuracy of the graph. A user can receive a plausible-looking but incorrect answer about where tasks run.

The earlier review also treated the narrower `ci-admin` check in `ci-configuration-tools/test.sh` as a merge blocker. After clarification that this directory is a reporting system and is not responsible for validating every CI configuration resource, that item is no longer considered a blocker for this tool.

## 1. Overlapping Pool Templates Resolve to the Wrong Node

Severity: P1  
Confidence: 10/10 for the algorithmic problem; confirmed in the bundled graph generated from the older `fxci-config` snapshot.

### Relevant code

In `ci-configuration-tools/taskcluster_cytoscape/graph.py`, templated pool IDs are converted into regular expressions:

```python
if "{" in pool_id and "}" in pool_id:
    pattern = re.sub(r"\{[^}]+\}", r"[^/]+", pool_id)
    pool_id_patterns.append((re.compile(f"^{pattern}$"), pool_node))
```

When a task does not have an exact pool match, the patterns are checked in their original YAML order and the first match wins:

```python
pool_node = pool_id_to_node.get(pool_key)
if not pool_node:
    for pattern, node in pool_id_patterns:
        if pattern.match(pool_key):
            pool_node = node
            break
```

### Failure mechanism

Consider pool templates such as:

```text
{pool-group}/b-linux{suffix}
{pool-group}/b-linux-medium-gcp
{pool-group}/b-linux-xlarge-gcp
{pool-group}/b-linux-kvm-gcp
```

The broad first template is transformed into approximately:

```regex
^[^/]+/b-linux[^/]+$
```

That expression matches all of these concrete pool IDs:

```text
gecko-3/b-linux-medium-gcp
gecko-3/b-linux-xlarge-gcp
gecko-3/b-linux-kvm-gcp
```

If the broad template appears before the specific templates, the loop stops at the broad match. Tasks intended for a specific pool are attached to the generic pool node. The specific node remains present but appears to have no tasks.

### Evidence in the bundled artifact

The committed `worker_pools_images.cyto.json` contains 25 task-group edges targeting the generic `pool_group_b_linuxsuffix` node. Specific medium, large, xlarge, and KVM pool nodes exist but have no corresponding task edges in that artifact.

The external `fxci-config` repository has changed since the bundled JSON was generated, and the exact broad template is no longer present in its current form. That does not remove the underlying issue: the algorithm is still order-dependent, and any future overlapping templates can reproduce the same corruption.

### Impact

- Task groups can be displayed as running on the wrong pool.
- Specific pool nodes can incorrectly appear unused.
- Queries such as “what tasks run on this system?” can return false answers.
- The output looks internally consistent, so users have little reason to suspect the result.

### Recommended fix

The strongest solution is to expand the configuration's `variants` into concrete pool IDs and match tasks exactly.

If regex/template matching remains necessary:

1. Collect all patterns matching a task pool instead of stopping at the first match.
2. Rank matches by specificity, using more literal characters and fewer placeholders as the stronger match.
3. Escape literal portions of pool IDs before building regexes.
4. Warn or fail generation when multiple matches have equal specificity.
5. Add fixture-based tests covering broad and specific overlapping templates.

A minimal improvement would sort patterns most-specific-first and emit ambiguity warnings, but explicit variant expansion would be more reliable.

## 2. Excluded Pools Return as Hardware Pools

Severity: P1 for correctness of the advertised exclusion feature  
Confidence: 10/10

### Relevant code

The exclusion is applied only while configured pools are loaded:

```python
for pool in pools.get("pools", []):
    pool_id = pool.get("pool_id")
    if args.pool_exclude and args.pool_exclude in pool_id:
        continue
```

Tasks are processed later without applying the same exclusion. An unmatched task pool falls into this branch:

```python
if pool_node:
    group_to_pools[group].add(pool_node)
else:
    # Hardware pool
    hwpool_node = sanitize_node_id(f"hwpool_{pool_key.replace('/', '_')}")
    add_node(hwpool_node, pool_key, "hwPoolNode", {"pool_key": pool_key})
    group_to_pools[group].add(hwpool_node)
```

### Concrete example

Suppose the pool configuration contains:

```text
{pool-group}/b-linux
```

and a task targets:

```text
gecko-3/b-linux
```

Running with `--pool-exclude b-linux` produces this flow:

1. The configured pool is skipped during pool loading.
2. Its exact and template mappings are never registered.
3. The task is still processed.
4. The task's pool has no registered match.
5. The fallback creates `gecko-3/b-linux` as an `hwPoolNode`.
6. The excluded pool remains visible, now with the wrong type and color.

### Impact

- `--pool-exclude` does not reliably exclude pools that have tasks.
- Excluded cloud pools can be mislabeled as hardware pools.
- Filtering changes the meaning of the graph instead of only removing selected data.

### Recommended fix

Apply exclusions to concrete task pool keys before attempting lookup or fallback:

```python
pool_key = f"{prov}/{wtype}"

if args.pool_exclude and args.pool_exclude in pool_key:
    continue
```

For template-aware behavior, retain the patterns for excluded pool templates and skip tasks matching any excluded pattern.

The fallback should also distinguish three states instead of treating every unmatched pool as hardware:

```text
configured cloud pool
known hardware pool
unknown or unresolved pool
```

An unknown pool may be absent because it was excluded, is new, comes from another configuration source, or failed template resolution. None of those conditions proves that it is hardware.

### Required tests

- Excluding an exact pool removes its node and all task edges.
- Excluding a templated pool removes concrete variants.
- An excluded pool is never recreated as `hwPoolNode`.
- An unknown pool is labeled unknown rather than automatically labeled hardware.

## Other Confirmed Issues

### Templated images have inconsistent type and class

Severity: P2  
Confidence: 10/10

For aliases containing placeholders, `graph.py` changes the most recently appended element's `data.type` and color to `imageNode` but leaves its Cytoscape class as `aliasNode`:

```python
if isinstance(alias, str) and "{" in alias and "}" in alias:
    elements[-2]["data"]["type"] = "imageNode"
    elements[-2]["data"]["color"] = NODE_COLORS["imageNode"]
    continue
```

The committed JSON contains two confirmed examples where `data.type` is `imageNode` and `classes` is `aliasNode`:

```text
ronin_blevel_windows11_a64_24h2_builder_suffix
ronin_t_windows11_a64_24h2_tester_suffix
```

The viewer uses classes for layout and styling but `data.type` for filters. These nodes can therefore be filtered as images while being positioned and colored as aliases.

The use of `elements[-2]` is also fragile. If `add_node` or `add_edge` deduplicates an element and does not append what this code expects, the branch can mutate an unrelated element. `add_node` should accept the desired type from the beginning or return the node record that was added/found.

### Generated JSON and viewer JSON use different locations

Severity: P2  
Confidence: 10/10 for local viewing and packaging

`graph.py` writes:

```python
with open("worker_pools_images.cyto.json", "w") as f:
```

When run from `taskcluster_cytoscape`, that creates:

```text
taskcluster_cytoscape/worker_pools_images.cyto.json
```

The viewer fetches a file relative to `viewer.html`:

```javascript
fetch('worker_pools_images.cyto.json')
```

That resolves to:

```text
taskcluster_cytoscape/cytoscape_viewer/worker_pools_images.cyto.json
```

The README instructs users to generate the graph and then serve the viewer directory, but it does not copy the fresh output into the viewer directory. `package_viewer.sh` also copies the viewer directory and therefore packages its existing, potentially stale JSON.

`deploy_to_gh_pages.sh` is not affected by this particular problem because it explicitly copies the newly generated parent-directory JSON to the deployment repository.

Recommended fix: make the output path explicit, write directly into the viewer directory by default, or have one build/package command copy the generated output into a staging directory used by local serving, packaging, and deployment.

### Unmatched does not necessarily mean hardware

Severity: P2  
Confidence: 10/10

The fallback in `graph.py` labels every unmatched task pool as `hwPoolNode`. The bundled graph consequently labels pools such as `built-in/succeed` and several `scriptworker-k8s/*` pools as hardware.

An unmatched pool can mean:

- it is genuinely hardware;
- it is defined in another source;
- it was excluded;
- a template failed to match;
- the input configurations are from different revisions;
- it is a built-in or special Taskcluster worker type.

The graph should use an explicit hardware classification source or display unmatched pools as unknown.

### Full Taskgraph loading has high memory cost

Severity: P2  
Confidence: 10/10

`tasks = load_json(tasks_path)` materializes the complete Taskgraph even though the script only makes a single pass over it. The current local `tasks.json` measured approximately 419 MB with 44,428 tasks. Loading it peaked at approximately 1.86 GB RSS and took about 12.8 seconds just to parse.

Recommended fix: stream top-level entries with `ijson.kvitems()`, update counters incrementally, and retain only the grouping state required for the final graph.

### Public JSON exposes local machine metadata

Severity: P2 privacy concern  
Confidence: 10/10

Generated metadata includes:

- hostname;
- username;
- full command line;
- absolute input paths;
- branch names;
- raw git remote URLs.

The checked-in artifact includes values such as `powderdry.local`, `aerickson`, and `/Users/aerickson/git/...`. `deploy_to_gh_pages.sh` publishes this JSON publicly. A credential-bearing HTTPS remote URL could also be serialized unchanged.

Recommended fix: publish only counts, timestamps, and commit SHAs. Remove hostname, username, command line, and absolute paths. Normalize repository URLs to public host/owner/repository identifiers with user information stripped.

### Local server binds all interfaces and serves the caller's directory

Severity: P2/P3 operational safety concern  
Confidence: 10/10

Both serving scripts run Python's HTTP server without a bind address or explicit directory. This binds to all interfaces and serves whichever directory the caller happens to be in. Running the script from the wrong directory can expose more of the local filesystem than intended.

Recommended Unix form:

```bash
python3 -m http.server 8080 \
  --bind 127.0.0.1 \
  --directory "$(cd -- "$(dirname -- "$0")" && pwd)"
```

Use equivalent loopback binding and script-directory resolution in PowerShell.

### Metadata rendering uses `innerHTML`

Severity: P3 under the current trusted-generation model  
Confidence: 9/10

Scalar metadata keys and values are inserted using `innerHTML`. A crafted command-line argument or other metadata value containing HTML could execute script when the viewer loads the JSON.

The current inputs are normally controlled by the person generating the graph, so this is not treated as a primary blocker. It should still use DOM elements plus `textContent`, particularly because the result is deployed publicly.

### Third-party scripts execute directly from a CDN

Severity: P3 supply-chain hardening  
Confidence: 9/10

The public viewer loads Cytoscape and its layout plugin directly from unpkg without Subresource Integrity or a Content Security Policy. A compromised CDN response would execute in every viewer session.

Preferred fix: vendor version-locked copies in the deployed artifact. A secondary option is to add verified `integrity` and `crossorigin` attributes plus a restrictive Content Security Policy.

### Poetry instructions and operating mode need adjustment

Severity: P2/P3 developer-experience issue

The project is a collection of scripts, but Poetry defaults to package mode and the configuration does not declare `package-mode = false`. The README also starts with `poetry shell`, which current Poetry provides through an optional plugin rather than its core installation.

Recommended configuration:

```toml
[tool.poetry]
package-mode = false
```

Recommended workflow:

```bash
poetry install
poetry run pytest
poetry run python taskcluster_cytoscape/graph.py ...
```

Alternatively document `poetry install --no-root` and the shell plugin requirement.

## Reclassified Item: Narrow `ci-admin` Check

The branch changes `ci-configuration-tools/test.sh` from a complete:

```bash
ci-admin check --environment firefoxci
```

to:

```bash
ci-admin check --environment firefoxci --resources worker_pools
```

The initial review treated this as a P1 because invalid non-worker-pool resources would no longer be checked by this script. The project owner clarified that this tool is a reporting system and is not responsible for those resources. Given that scope, this is not considered a blocker for the Cytoscape reporting tool.

It may still be worth naming the script or command as a fast worker-pool-specific check so readers do not mistake it for comprehensive repository validation.

## Scope and Repository State

- PR description: adds scripts and a Taskcluster Cytoscape viewer.
- Diff against `upstream/master`: 21 files, approximately 1,554 insertions and 2 deletions.
- The branch was 10 upstream commits behind `master` at review time.
- GitHub reported the PR as mergeable.
- GitHub reported no automated PR checks.
- GitHub had no review comments or submitted reviews for the PR.
- Existing untracked files in `moonshot` were not modified during review.

## Verification Performed

### Tests

```text
20 passed in 0.01s
```

The tests cover `extract_group` and `collapse_wildcards` in `summarize_tasks.py`. They do not exercise `graph.py`, pool resolution, exclusions, JSON placement, metadata generation, shell workflows, or the browser viewer.

### Other checks

- All changed shell scripts passed `bash -n` syntax checks.
- `shellcheck` reported quoting/style suggestions but no critical shell failure.
- `poetry check --lock` passed with a warning that the table form of `project.license` is deprecated.
- `git diff --check` found one trailing blank-line warning in `serve.sh`.

## Recommended Test Matrix for `graph.py`

Add small YAML/JSON fixtures and verify the generated element set directly:

1. Exact pool IDs take precedence over templates.
2. A specific template beats a broad overlapping template regardless of YAML order.
3. Equal-specificity matches produce a visible ambiguity error.
4. Excluded exact pools produce no node or edge.
5. Excluded templates remove all matching concrete task pools.
6. Unknown pools are not automatically classified as hardware.
7. Node `classes` always agrees with `data.type`.
8. Repeated aliases cannot cause an unrelated `elements[-2]` mutation.
9. Generated JSON is written to the exact file consumed by local serving and packaging.
10. Public metadata omits local usernames, hostnames, absolute paths, and credential-bearing URLs.

## Suggested Landing Order

1. Fix template matching and add ambiguity detection.
2. Fix exclusion handling and introduce an unknown pool type.
3. Add focused `graph.py` fixture tests for both fixes.
4. Unify generated JSON, local viewing, packaging, and deployment around one staging/output path.
5. Fix image node type/class construction.
6. Remove private metadata from public artifacts.
7. Stream the Taskgraph if the tool needs to run reliably on constrained machines or in CI.
8. Rebase onto the current `master` and add an automated test job if this tool will be maintained long term.
