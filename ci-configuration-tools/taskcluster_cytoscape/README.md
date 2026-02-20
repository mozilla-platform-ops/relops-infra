# taskcluster_cytoscape

## Overview

Visualizes Firefox Taskcluster tasks, worker pools, image aliases, and images using [Cytoscope.js](https://js.cytoscape.org/).

Task data is from [Firefox](https://github.com/mozilla-firefox/firefox)'s Taskgraph. All other data is from [fxci-config](http://github.com/mozilla-releng/fxci-config).

This software consists of a Python script to generate a Cytoscape.js JSON file (`graph.py`) and the associated HTML viewer for the JSON file (see the `cytoscape_viewer/` directory).

## Generation of JSON

1. Run `poetry shell && poetry install` one directory up.
1. Update firefox repo and generate tasks.json in firefox repo.
1. Update the fxci-config repo.
1. Run `./generate_graph.sh` (paths may need to be edited).

## Viewing

See the README.md inside the `cytoscape_viewer` directory.

## TODOs

### Viewer

1. Deeplinking of search query and 'show only linked' params.
1. Clicking on a search result will center view on it.
1. Clicking search will run 'clear highlights' first. Currently accumulates.

### Graph

1. For step 'Generation of JSON', for tasks.json, just fetch from 'https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/gecko.v2.mozilla-central.latest.taskgraph.decision/artifacts/public%2Ftask-graph.json'.
1. Manage repos (don't take paths to them, do shallow clones)
1. Fetch tasks.json from tc index task:
    - https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/gecko.v2.mozilla-central.latest.taskgraph.decision/artifacts/public%2Ftask-graph.json
