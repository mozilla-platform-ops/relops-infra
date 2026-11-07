# Taskcluster Web Launcher

A Firefox CI worker picker that finds worker-type pages and keeps frequently used choices at the top. Requires Python 3 and a terminal; there are no third-party dependencies.

```sh
./tcwl
./tcwl gecko-t-linux-talos-2404-wayland
./tcwl --print releng-hardware/gecko-t-linux-talos-2404-wayland
```

Type to filter, use the arrow keys to select, and press Enter to open in your browser. Ctrl-P prints the selected URL instead. Paste a full Firefox CI worker-type URL to open it directly; its query string is discarded. Esc or Ctrl-C quits, and Ctrl-R refreshes the live list. When offline, previously used entries remain available, and you can type a full worker type or URL.

The picker loads worker types from Firefox CI for `releng-hardware` by default. Use `--provisioner NAME` for another provisioner, or provide `PROVISIONER/WORKER-TYPE` directly. History from all provisioners is searchable. Each successful open or print increments its count, with ties broken by recency. Data is stored in `~/.tc-worker-picker.sqlite3`; use `--db PATH` to choose another location.

The live list is loaded in the background and is subject to Taskcluster API availability and access. An error appears in the picker when discovery fails; saved and manually entered choices still work.

After a successful refresh, saved worker types absent from the selected provisioner's live list are marked `missing` in red. They remain selectable: absence from the active list does not prove a pool was permanently deleted. Other provisioners are not checked, and a failed or pending refresh does not mark entries missing.

Run the tests from this directory with `uv run --with pytest --no-project pytest -q test_worker_picker.py`.
