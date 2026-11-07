# Taskcluster Web Launcher

A Firefox CI worker picker that finds worker-type pages and keeps frequently used choices at the top. Requires Python 3 and a terminal; there are no third-party dependencies.

```sh
./tcwl
./tcwl gecko-t-linux-talos-2404-wayland
./tcwl --print releng-hardware/gecko-t-linux-talos-2404-wayland
```

Type to fuzzy search, use Up/Down to select, and press Enter to open in your browser. Left/Right move one page up/down, as do Page Up/Page Down. Exact and substring matches rank ahead of matches with characters in order; usage history breaks ties. Ctrl-P prints the selected URL instead. Paste a full Firefox CI worker-type URL to open it directly; its query string is discarded. Esc or Ctrl-C quits, and Ctrl-R forces a refresh. When offline, cached and previously used entries remain available, and you can type a full worker type or URL.

The picker preloads worker types for `releng-hardware` and `proj-autophone`, displayed as `provisioner/pool-name`. It also loads the list of provisioner names, without fetching all their workers. Fuzzy search for another provisioner (for example, `gkt` for `gecko-t`) and select its provisioner row to load its workers. Typing a complete provisioner name such as `gecko-t` or `gecko-t/` loads it automatically. You can then add a worker search, such as `gecko-t linux`.

Use `--provisioner NAME` to restrict discovery to one provisioner, or provide `PROVISIONER/WORKER-TYPE` directly. Bare worker names use `releng-hardware`, or the provisioner specified with the flag. History from all provisioners is searchable. Each successful open or print increments its count, with ties broken by recency. Data is stored in `~/.tc-worker-picker.sqlite3`; use `--db PATH` to choose another location.

Provisioner names and requested worker lists are cached in the same database for one hour. Cached lists appear immediately; expired lists refresh in the background and remain available if the refresh fails. Ctrl-R bypasses the cache for provisioner names and all worker lists requested in this session. Other provisioners' worker lists are only read from cache or fetched when requested.

The live list is loaded in the background and is subject to Taskcluster API availability and access. An error appears in the picker when discovery fails; saved and manually entered choices still work.

After a successful refresh, saved worker types absent from that provisioner's live list are marked `missing` in red. They remain selectable: absence from the active list does not prove a pool was permanently deleted. Cached lists, failed requests, and pending refreshes do not mark entries missing. Each provisioner's refresh is tracked separately.

Run the tests from this directory with `uv run --with pytest --no-project pytest -q test_worker_picker.py`.
