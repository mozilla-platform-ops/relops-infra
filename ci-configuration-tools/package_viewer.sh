#!/usr/bin/env bash

set -e
set -x

dest="taskcluster_cytoscape_viewer.zip"

rm -f "$dest"

# compress the `cytoscape_viewer` directory with zip, resolving symlinks and also excluding the `old` dir
# -r or --recurse-paths: Recursively include directories.
# -y: Follow symbolic links (include the referenced file, not the symlink itself).
# zip -r -y "$dest" cytoscape_viewer -x "cytoscape_viewer/old/*"



cp -aL cytoscape_viewer taskcluster_cytoscape_viewer
zip -r "$dest" taskcluster_cytoscape_viewer
rm -r taskcluster_cytoscape_viewer
