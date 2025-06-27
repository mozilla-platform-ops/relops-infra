# taskcluster_cytoscape

## Overview

This software consists of a Python script to generate a Cytoscape.js JSON file and the associated HTML viewer for the JSON file.

## Generation of JSON

1. Run `poetry shell && poetry install` one directory up.
1. Update firefox repo and generate tasks.json in firefox repo.
1. Update the fxci-config repo.
1. Run `./generate_graph.sh` (paths may need to be edited).

## Viewing

See the README.md inside the `cytoscape_viewer` directory.