#!/usr/bin/env node

// example usage:
// [type] [group] [quarantine_time] [expiration] [hostnames]
// ./quarantine_tc.js gecko-t-linux-talos mdc1 "1 year" "1 day" t-linux64-ms{226..240}
//

var taskcluster = require('taskcluster-client');
taskcluster.config(taskcluster.fromEnvVars());
var q = new taskcluster.Queue({rootUrl:'https://firefox-ci-tc.services.mozilla.com'});
var provisionerId = 'releng-hardware';
var workerType = process.argv[2];
var workerGroup = process.argv[3];
process.argv.slice(6).forEach(function(workerId) {
    console.log(workerId);
    q.getWorker(provisionerId, workerType, workerGroup, workerId)
        .then(function(a) {
            console.log('expires:', a['expires']);
            console.log('quarantine:', a['quarantineUntil']);
            console.log('--');
        }, error => { console.log('caught', error.message); })
        .finally(function(a) {
            if (process.argv[5] != ' ') {
                var exp = taskcluster.fromNow(process.argv[5]);
            } else {
                var exp = taskcluster.fromNow("1 year");
            }
            q.declareWorker(provisionerId, workerType, workerGroup, workerId, {"expires":exp})
                .then(function(a) {
                    q.getWorker(provisionerId, workerType, workerGroup, workerId)
                        .then(function(a){
                            console.log(a['workerId'], 'expires:', a['expires']);
                            if (process.argv[4] != ' ') {
                                var quar = taskcluster.fromNow(process.argv[4]);
                                q.quarantineWorker(provisionerId, workerType, workerGroup, workerId, {"quarantineUntil":quar})
                                    .then(function(a) {
                                        q.getWorker(provisionerId, workerType, workerGroup, workerId)
                                            .then(function(a){
                                                console.log(a['workerId'], 'quarantine:', a['quarantineUntil']);
                                            });
                                    })
                                    .catch(function(z) {
                                        console.warn(z);
                                    });
                            }
                        });
                })
                .catch(function(z) {
                    console.warn(z);
                });
        });
});
