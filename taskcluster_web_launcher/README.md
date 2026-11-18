# Taskcluster Web Launcher

A Firefox CI worker pool picker that opens worker-type pages and keeps frequently used choices at the top. Requires Python 3 and a terminal; there are no third-party dependencies.

```sh
./tcwl
./tcwl gecko-t-linux-talos-2404-wayland
./tcwl --print releng-hardware/gecko-t-linux-talos-2404-wayland
```

Type to fuzzy search, use Up/Down to select, and press Enter to open a pool in your browser or browse a provisioner. Left/Right move one page up/down, as do Page Up/Page Down. Exact and substring matches rank ahead of matches with characters in order; usage history breaks ties. Matched characters are highlighted, and selection stays on the same target as background results arrive. Long paths keep both their beginning and end visible.

Ctrl-P prints the selected pool's URL instead of opening it. Paste a full Firefox CI worker-type URL to open it directly; its query string is discarded. Ctrl-U clears the search, Ctrl-R forces a refresh, and Ctrl-C quits. Esc clears a typed filter first, then returns from a provisioner to the home view. At home with an empty filter, Esc keeps the picker open. When offline, cached and previously used entries remain available, and you can type a full pool name or URL.

The picker preloads pools for `releng-hardware` and `proj-autophone`, displayed as `provisioner/pool-name`. It also loads the list of provisioner names, without fetching all their pools. Provisioner rows have a `›` marker and a pool count or a `Browse` label. Fuzzy search for another provisioner (for example, `gkt` for `gecko-t`) and select its row to load and browse its pools. Typing a complete provisioner name such as `gecko-t` or `gecko-t/` loads it automatically. You can also search across loaded pools, for example `gecko-t linux`.

The home view has two sections: **Frequently used**, ranked by usage count and recency, and **Provisioners**, sorted alphabetically. Usage counts describe automatic history, rather than pinned favorites. Other pools appear when searching or browsing their provisioner.

Selecting a provisioner opens a scoped view with its name in the header and a separate pool search. Enter opens the selected pool; unmatched bare names use that provisioner. Ctrl-B (**All provisioners**) returns home immediately; Esc or Backspace returns home when the search is empty, with the provisioner selected. Selecting a preloaded provisioner uses its loaded list immediately.

Use `--provisioner NAME` to start scoped to one provisioner and discover only its pools until you choose **All provisioners**, or provide `PROVISIONER/WORKER-TYPE` directly. Bare pool names from home use `releng-hardware`, or the provisioner specified with the flag. History from all provisioners is searchable from home. Each successful open or print increments its count, with ties broken by recency. Data is stored in `~/.tc-worker-picker.sqlite3`; use `--db PATH` to choose another location.

Provisioner names and requested worker lists are cached in the same database for one hour. Cached lists appear immediately; expired lists refresh in the background and remain available if the refresh fails. Ctrl-R bypasses the cache for provisioner names and all worker lists requested in this session. Other provisioners' worker lists are only read from cache or fetched when requested.

Lists load in the background and are subject to Taskcluster API availability and access. Loading and refresh failures appear beside the affected provisioner. Details for a selected provisioner or its pools appear in the status line; saved and manually entered choices still work. Pool counts describe discovered choices, not worker health or capacity.

After a successful refresh, saved worker types absent from that provisioner's live list are marked `missing` in red. They remain selectable: absence from the active list does not prove a pool was permanently deleted. Cached lists, failed requests, and pending refreshes do not mark entries missing. Each provisioner's refresh is tracked separately.

Run the tests from this directory with `uv run --with pytest --no-project pytest -q test_worker_picker.py`.
