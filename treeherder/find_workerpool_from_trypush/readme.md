# Find worker pool tasks from try push

```Powershell
. .\Functions.ps1

$params = @{
    WorkerPool = "gecko-1/b-win2022-alpha"
    Revision = "923f51ea41a5d0798a1cc86a5ceaf51dae812bf5"
    ClientID = "foo"
    AccessToken = "bar"
    Branch = "mozilla-central"
}

Get-WorkerPoolTasks @Params

```
* Run [treeherder.ps1](.\treeherder.ps1)