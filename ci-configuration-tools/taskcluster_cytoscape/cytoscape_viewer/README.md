# Taskcluster Cytoscape Viewer

Visualizes Firefox Taskcluster tasks, worker pools, image aliases, and images using [Cytoscope.js](https://js.cytoscape.org/).

Task data is from [Firefox](https://github.com/mozilla-firefox/firefox)'s Taskgraph. All other data is from [fxci-config](http://github.com/mozilla-releng/fxci-config).

## Why?

Taskgraph is complicated and we frequently get questions like 'What tasks run on these systems?' and 'What pools use these images?'.

This tool aims at helping people find those answers themselves.

## Caveats

- We lack task data for other repositories that submit tasks to fxci. For example, the translations GPU workers don't have any tasks in the dataset. Similarly, the VPN workers don't have any tasks.
- Known Firefox DOM rendering bug (https://bugzilla.mozilla.org/show_bug.cgi?id=1974854). Chrome doesn't have this issue.
- Hardware pools without tasks won't be visible. Consider scanning fxci taskcluster? Could be useful to be able to identify them.

## How To Run on Web

Currently deployed to: [https://aerickson.github.io/gh_pages_test/viewer.html](https://aerickson.github.io/gh_pages_test/viewer.html)

## How To Run Locally

### Unix

```bash
./serve.sh
# surf to http://localhost:8080/viewer.html
```

### Windows

Untested. Please report back.

```bash
# in powershell
serve.ps1
# surf to http://localhost:8080/viewer.html
```

## Usage

You scroll around the display area by clicking and dragging. Zooming also works.

Click around and explore for now. Send me feedback.

### Color Guide

```
Purple: tasks
Light Green: cloud pool
Dark Green: hw pool
Light Blue: image alias
Light Yellow: l1 image
Light Pink: l3 image
```

### Example 1: Find out where `perftest-*-summarizer*` tasks run.

1. Type `summarizer` into search. Press return.
2. Click the `Show only linked` button.
3. Make it easier to view by Layout>Tidytree then click `Apply Layout`.

### Example 2: Find out which pools and tasks use the `docker-worker-gcp-u14-04-2025-06-16` (docker-worker) image.

1. Type `fxci-level1-gcp: docker-worker-gcp-u14-04-2025-06-16` into search. Press return.
2. Click the `Show only linked` button.
3. Make it easier to view by Layout>Tidytree then click `Apply Layout`.
