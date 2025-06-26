# taskcluster cytoscape viewer

Visualizes Taskcluster tasks, worker pools, image aliases, and images.

Task data is from Firefox's Taskgraph. All other data is from fxci-config.

## why

Taskgraph is complicated and we frequently get questions like 'What tasks run on these systems?' and 'What pools use these images?'.

This tool aims at helping people find those answers themselves.

## how to run

### unix

```bash
./serve.sh
# surf to http://localhost:8080
```

### windows

Untested. Please report back.

```bash
# in powershell
serve.ps1
# surf to http://localhost:8080
```

## usage

TODO... click around and explore for now. Send me feedback.

### Color Guide

Purple: tasks
Light Green: cloud pool
Dark Green: hw pool
Light Blue: image alias
Light Yellow: l1 image
Light Pink: l3 image

### Example 1: Find out where perftest-*-summarizer* tasks run.

1. Type `summarizer` into search. Press return.
2. Click the `Show only linked` button.
3. Make it easier to view by Layout>Tidytree then click `Apply Layout`.