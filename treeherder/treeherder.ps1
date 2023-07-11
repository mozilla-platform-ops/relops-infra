function Get-TaskCluster {
    ## Check for taskcluster binary in path
    if (-Not (Get-Command Taskcluster)) {
        $false
    }
    else {
        $true
    }
}

function Get-TreeHerderPushID {
    [CmdletBinding()]
    param (
        [String]
        $Revision
    )

    (Invoke-RestMethod "https://treeherder.mozilla.org/api/project/try/push/?full=true&count=10&revision=$revision").results.id

}

function Get-TreeHerderPushStatus {
    [CmdletBinding()]
    param (
        [String]
        $Push_Id
    )
    
    ## Check if tests are still running
    $results = Invoke-RestMethod "https://treeherder.mozilla.org/api/jobs/" -Body @{
        push_id = $push_id
    } 
    
    $results.results | ForEach-Object {
        if ($PSItem -eq "pending") {
            [PSCustomObject]@{
                Status = "Pending"
            }
        }
    }
}

if (-Not (Get-TaskCluster)) {
    throw "taskcluster not found in path"
}

$revision = "659725318f9909c795c32301236c257697f683c9"
$push_id = Get-TreeHerderPushID -Revision $revision

## Set taskcluster variables
$ENV:TASKCLUSTER_CLIENT_ID = "xxxxx"
$ENV:TASKCLUSTER_ACCESS_TOKEN = "yyyy"
$ENV:TASKCLUSTER_ROOT_URL = "https://firefox-ci-tc.services.mozilla.com/"

## Check the status of the try push
$status = Get-TreeHerderPushStatus -Push_Id $push_id

## If the try push is still running, don't continue
if ($status -match "Pending") {
    throw "Revision $revision still running. Check https://treeherder.mozilla.org/jobs?repo=try&revision=$revision"
}

## Once the try push has ran through all tests, then identify failures
## Get all failed tests for the revision
$try_push_failures = Invoke-RestMethod "https://treeherder.mozilla.org/api/jobs/" -Body @{
    push_id                   = $push_id
    failure_classification_id = 6 ## per Joel Maher
}

## Create lookup table for API key/value pairs
$wee = $try_push_failures.job_property_names
$ht = @{}
$i = 0
foreach ($p in $wee) {
    $ht.add($i++, $p)
}

## loop through each failed job and set name/value output 
## NOTE: this is the first attempt at organizing failed runs
$failed_jobs_orig = for ($i = 0; $i -lt $try_push_failures.count; $i++) {
    $collection = $try_push_failures.results[$i]
    [PSCustomObject]@{
        "failure_classification_id" = $collection[0]
        "id"                        = $collection[1]
        "job_group_name"            = $collection[2]
        "job_group_symbol"          = $collection[3]
        "job_type_name"             = $collection[4]
        "job_type_symbol"           = $collection[5]
        "last_modified"             = $collection[6]
        "platform"                  = $collection[7]
        "push_id"                   = $collection[8]
        "push_revision"             = $collection[9]
        "result"                    = $collection[10]
        "signature"                 = $collection[11]
        "state"                     = $collection[12]
        "tier"                      = $collection[13]
        "task_id"                   = $collection[14]
        "retry_id"                  = $collection[15]
        "duration"                  = $collection[16]
        "platform_option"           = $collection[17]
    }
}

$failed_jobs_orig
break

## re-run each failed task again
$failed_jobs_orig | ConvertFrom-Json | ForEach-Object {
    $task = $PSItem.task_id
    taskcluster task rerun $task
}

## Check the status of the original failed tasks that are re-ran
$status_1 = $failed_jobs_orig | ForEach-Object {
    $task = $PSItem.task_id
    taskcluster api queue status $task | ConvertFrom-Json | Select-Object -ExpandProperty status
}

break

## Get the tasks which failed again after the first re-run
$failed_2 = $status_1 | Where-Object {
    $psitem.state -match "failed|exception"
}

## For each of the failed again tasks, re-run them for the 2nd time
$failed_2 | ForEach-Object {
    $task = $PSItem.taskId
    taskcluster task rerun $task
}

## Check the status of the 2nd time re-running
$status_3 = $failed_2 | ForEach-Object {
    $task = $PSItem.taskId
    taskcluster api queue status $task | ConvertFrom-Json | Select-Object -ExpandProperty status
}

break

## Store the results of the tasks that fail 3 times in a row
$failed_3 = $status_3 | Where-Object {
    $psitem.state -eq "failed"
}

## Send the output to Joel
$failed_3
