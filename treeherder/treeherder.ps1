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

$revision = "ef9f7b7091d9d8ad7487521f8db90bd3d8aa07a9"
$push_id = Get-TreeHerderPushID -Revision $revision

## Set taskcluster variables
$ENV:TASKCLUSTER_CLIENT_ID = "foo"
$ENV:TASKCLUSTER_ACCESS_TOKEN = "bar"
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
$failed_1 = for ($i = 0; $i -lt $try_push_failures.count; $i++) {
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

## re-run each failed task again
$failed_1 | ForEach-Object {
    $task = $PSItem.task_id
    taskcluster task rerun $task
}

## Check the status of the original failed tasks that are re-ran
$status_1 = $failed_1 | ForEach-Object {
    $task = $PSItem.task_id
    taskcluster api queue status $task | ConvertFrom-Json | Select-Object -ExpandProperty status
}

## Loop through the failed tests and check their status until complete
do {
    Start-Sleep -Seconds 30
    $status_1 = $failed_1 | ForEach-Object {
        $task = $PSItem.task_id
        taskcluster api queue status $task | ConvertFrom-Json | Select-Object -ExpandProperty status
    }    
    $running = ($status_1.state|Group-Object|Where-Object{$PSItem.name -eq "Running"}).count
    Write-host "Processing $running running jobs | $(Get-Date)"
} until (
    $status_1.state -notcontains "running"
)


## Get the tasks which failed again after the first re-run
$failed_2 = $status_1 | Where-Object {
    $psitem.state -match "failed|exception"
}

## For each of the failed again tasks, re-run them for the 2nd time
$failed_2 | ForEach-Object {
    $task = $PSItem.taskId
    taskcluster task rerun $task
}

## Loop through the failed tests and check their status until complete
do {
    Start-Sleep -Seconds 30
    $status_3 = $failed_2 | ForEach-Object {
        $task = $PSItem.taskId
        taskcluster api queue status $task | ConvertFrom-Json | Select-Object -ExpandProperty status
    }
    $running = ($status_3.state|Group-Object|Where-Object{$PSItem.name -eq "Running"}).count
    Write-host "Processing $running running jobs | $(Get-Date)"
} until (
    $status_3.state -notcontains "running"
)

## Store the results of the tasks that fail 3 times in a row
$failed_3 = $status_3 | Where-Object {
    $psitem.state -eq "failed"
}

## For each of the failed again tasks, re-run them for the 3rd time
$failed_3 | ForEach-Object {
    $task = $PSItem.taskId
    taskcluster task rerun $task
}

## Loop through the failed tests and check their status until complete
do {
    Start-Sleep -Seconds 30
    $status_4 = $failed_3 | ForEach-Object {
        $task = $PSItem.taskId
        taskcluster api queue status $task | ConvertFrom-Json | Select-Object -ExpandProperty status
    }
    $running = ($status_4.state|Group-Object|Where-Object{$PSItem.name -eq "Running"}).count
    Write-host "Processing $running running jobs | $(Get-Date)"
} until (
    $status_4.state -notcontains "running"
)

## Group them by test name from the originally failed tests
$final = $failed_1 | Where-Object {
    $psitem.task_id -in $failed_3.taskId
}

## Output the final result to json and export to local homedir
$final | ConvertTo-Json | Out-File "~/$($revision)_failed.json"