#!/usr/bin/env node

// example usage:
// node list_quar.js gecko-t-linux-talos  # to list quarantined workers
// node list_quar.js gecko-t-linux-talos 1 # to list non-quarantined workers

var taskcluster = require('taskcluster-client');
taskcluster.config(taskcluster.fromEnvVars());
var q = new taskcluster.Queue({rootUrl:'https://firefox-ci-tc.services.mozilla.com'});
var provisionerId = 'releng-hardware';
var workerType = process.argv[2];
var only_quarantined = (process.argv[3] == 'true' || 1 != Number.parseInt(process.argv[3]));
function print_with_task(runId=0, worker=Null, task) {
    if ('runs' in task['status']) {
        last_task = task['status']['runs'][runId];
        state = last_task['state'];
        if ('resolved' in last_task) {
            last_time = last_task['resolved'];
        } else {
            last_time = last_task['started'];
        }
    } else {
        last_time = task['scheduled'];
        state = 'null';
    }
    console.log(worker['workerGroup'], worker['workerId'], worker['quarantineUntil'], last_time, state);
}
q.listWorkers(provisionerId, workerType, {'quarantined':only_quarantined})
    .then(function(a) {
        // console.log(a);
        workers = a['workers'];
        for (n in workers) {
            w = workers[n];
            t = w['latestTask'];
            if (t !== undefined && 'runId' in t) {
                runId = t['runId'];
                q.status(t['taskId'])
                    .then(print_with_task.bind(null, runId, w),
                        error =>
                            console.log.bind(null, w['workerGroup'], w['workerId'], w['quarantineUntil'], '-', 'null')
                        );
            } else {
                console.log(w['workerGroup'], w['workerId'], w['quarantineUntil'], '-', 'null');
            }
        }
    }, error =>
        console.log.bind(null, w['workerGroup'], w['workerId'], w['quarantineUntil'], '-', 'null')
    )
        //console.log('caught', error.message);
    .finally(function(a) {
    });
