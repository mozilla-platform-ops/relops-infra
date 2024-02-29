$url = "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/gecko.v2.mozilla-central.latest.taskgraph.decision/artifacts/public%2Ftask-graph.json"
$tg = Invoke-RestMethod -Uri $url

## dump the raw contents of the json file to tg_extract
$tg_extract = ForEach ($noteProperty in $tg.psobject.Properties) {
    $noteProperty.Value
}

## Loop through the task-graph and look for b-win2022 as the workertype
$builder = ForEach ($noteProperty in $tg.psobject.Properties) {
    if ($noteProperty.Value.task.workerType -eq "b-win2022") {
        [PSCustomObject]@{
            Name = $noteProperty.Name
            Value = $noteProperty.Value
        }
    }
}

## get all where dependencies has a value

## Foreach result in builder, find the dependencies and the taskid of the depdenent task
$builder_labels = $builder.Value.dependencies | ForEach-Object {
    [PSCustomObject]@{
        Name = $PSItem.psobject.Properties | ForEach-Object {$psitem}
    }
} | Select-Object -ExpandProperty Name | ForEach-Object {
    [PSCustomObject]@{
        Label = $PSItem.Name
        Task = $PSItem.Value
    }
} #| Select-Object -ExpandProperty Label -Unique | Sort-Object

## run a try push against the labels/routes
$builder.value.task.routes | Where-Object {$PSItem -match "central\.latest"} | Sort-Object

$final = [PSCustomObject]@{
    Routes = $builder.value.task.routes | Where-Object {$PSItem -match "central\.latest"} | Sort-Object
    Labels = $builder_labels
}