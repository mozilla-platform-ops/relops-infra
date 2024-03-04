$url = "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/gecko.v2.mozilla-central.latest.taskgraph.decision/artifacts/public%2Ftask-graph.json"
$tg = Invoke-RestMethod -Uri $url

$ENV:TASKCLUSTER_ROOT_URL = "https://firefox-ci-tc.services.mozilla.com/"

## Loop through the task-graph and look for b-win2022 as the workertype
$builder = ForEach ($noteProperty in $tg.psobject.Properties) {
    if ($noteProperty.Value.task.workerType -eq "b-win2022") {
        [PSCustomObject]@{
            TaskID = $noteProperty.Name
            Data = $noteProperty.Value
        }
    }
}

## find the dependencies of each task
$builder_labels = $builder | ForEach-Object {
    ## does this have a dependency?
    $deps = $psitem.Data.dependencies.psobject.Properties | ForEach-Object {$PSItem}
    $data = $psitem.Data
    if ($null -ne $deps) {
        [PSCustomObject]@{
            TaskID = $data.task_id
            Data = $data
            Label = $data.label
            HasDependencies = $true
            DependencyTask = $deps.Value
            DependencyName = $deps.name
        }
    }
    else {
        [PSCustomObject]@{
            TaskID = $data.task_id
            Data = $data
            Label = $data.label
            HasDependencies = $false
            DependencyTask = $deps.Value
            DependencyName = $deps.name
        }
    }
}

## Output the labels
$builder_labels.Label | Sort-Object

## generate a try task config
$try_task_config = [Ordered]@{
    parameters = [Ordered]@{
        optimize_target_tasks = $false
        try_task_config = [Ordered]@{
            env = @{
                TRY_SELECTOR = "fuzzy"
            }
            tasks = $builder_labels.Label | Sort-Object
        }
        'worker-overrides' = @{
            'b-win2022' = 'gecko-1/b-win2022-alpha'
        }
    }
    version = 2
}

#$try_task_config | ConvertTo-Json -Depth 99 | Set-Clipboard