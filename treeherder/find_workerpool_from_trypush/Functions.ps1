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

function Get-WorkerPoolTasks {
    [CmdletBinding()]
    param (
        [String]
        $WorkerPool,

        [String[]]
        $Revision,

        [String]
        $ClientID,

        [String]
        $AccessToken,

        [String]
        $Branch = "mozilla-central"
    )
    

    ## Loop through all of the revisions
    $Revision | ForEach-Object {
        if (-Not (Get-TaskCluster)) {
            throw "taskcluster not found in path"
        }
        $rev = $PSItem
        $push_id = (Invoke-RestMethod "https://treeherder.mozilla.org/api/project/$($branch)/push/?full=true&count=10&revision=$rev" -TimeoutSec 5).results.id
        if ($null -eq $push_id) {
            $push_id = (Invoke-RestMethod "https://treeherder.mozilla.org/api/project/try/push/?full=true&count=10&revision=$rev").results.id
        }
    
        ## Set taskcluster variables
        $ENV:TASKCLUSTER_CLIENT_ID = $ClientID
        $ENV:TASKCLUSTER_ACCESS_TOKEN = $AccessToken
        $ENV:TASKCLUSTER_ROOT_URL = "https://firefox-ci-tc.services.mozilla.com/"
    
        $tryjob = Invoke-RestMethod "https://treeherder.mozilla.org/api/jobs/" -Body @{
            push_id = $push_id
        }
    
        ## Create lookup table for API key/value pairs
        $wee = $tryjob.job_property_names
        $ht = @{}
        $i = 0
        foreach ($p in $wee) {
            $ht.add($i++, $p)
        }
    
        $tc_results = for ($i = 0; $i -lt $tryjob.count; $i++) {
            $collection = $tryjob.results[$i]
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
    
        ## foreach task, get the routes
        foreach ($task in $tc_results) {
            $task_def = taskcluster task def $task.task_id | ConvertFrom-Json
            if ($task_def.taskQueueId -eq $WorkerPool) {
                [PSCustomObject]@{
                    Task           = $task
                    TaskDefinition = $task_def
                    Revision       = $rev
                    TaskId         = "https://firefox-ci-tc.services.mozilla.com/tasks/$($task.task_id)"
                }
            }
        }
    }

}
