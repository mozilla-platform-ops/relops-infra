# Treeherder

## Requirements

* Taskcluster client credentials
* A completed try push and its revision string

## To-Do

* Add some form of caching so I can cache the failures each time
* Add logging

## How to run

* NOTE: This is requires a completed try push without any pending tasks
* NOTE: This script takes a while since it relies heavily on re-running failed tests several times 
* NOTE: 
* Launch powershell window using VS Code or Powershell ISE window
* Set the environment variables locally so you can re-run using taskcluster client

```Powershell
$ENV:TASKCLUSTER_CLIENT_ID = "foo"
$ENV:TASKCLUSTER_ACCESS_TOKEN = "bar"
$ENV:TASKCLUSTER_ROOT_URL = "https://firefox-ci-tc.services.mozilla.com/"
```
* Run [treeherder.ps1](.\treeherder.ps1)
* The script should exit if