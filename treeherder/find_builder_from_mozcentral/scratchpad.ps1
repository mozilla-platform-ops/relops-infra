Import-Module ..\functions.psm1 -Force

## get the latest task/push on mozilla-central to find the decision task
$latest = Invoke-RestMethod "https://treeherder.mozilla.org/api/project/mozilla-central/push/?full=true&count=10"

## get the latest revision
$rev = $latest.results[0].revision

## get the decision task taskid
$dtSplat = @{
    Revision = $rev
    ClientID = ""
    AccessToken = ""
}
$dt = Get-DecisionTaskJson @dtSplat

## use the decision task task id to pull the task-graph
$taskId = $dt.task.task_id
$tg = Invoke-RestMethod "https://firefoxci.taskcluster-artifacts.net/$($taskID)/0/public/task-graph.json"

## Loop through the task-graph and look for b-win2022 as the workertype
$builder = ForEach ($noteProperty in $tg.psobject.Properties) {
    if ($noteProperty.Value.task.workerType -eq "b-win2022") {
        $noteProperty.Value
    }
}

## find the labels
$builder.task.tags.label | Sort-Object

## run a try push against the labels/routes
$builder.task.routes | Where-Object {$PSItem -match "central\.latest"} | Sort-Object
