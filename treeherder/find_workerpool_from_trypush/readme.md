# Find worker pool tasks from try push

## Requirements

* Install Taskcluster client locally

```Powershell
. .\Functions.ps1

$params = @{
    WorkerPool = "gecko-1/b-win2022-alpha" ## worker pool you're wanting to test against
    Revision = "923f51ea41a5d0798a1cc86a5ceaf51dae812bf5" ## revision of your push
    ClientID = "foo" ## tc client id
    AccessToken = "bar" ## tc secret
    Branch = "mozilla-central" ## branch you're pushing to, will also look at try
}

$results = Get-WorkerPoolTasks @Params

## Lots of information for each task found that runs on the worker pool
$results.task

## Task definition for each task
$results.taskdefinition

## URL of the task that runs on your worker pool
$results.taskID

```