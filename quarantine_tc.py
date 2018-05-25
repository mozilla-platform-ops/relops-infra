#!/usr/bin/env python

# https://gist.github.com/catlee/9f85b4d51425a41cdc33ab8c7b754507/revisions

from __future__ import print_function
from taskcluster import Queue
from taskcluster.utils import fromNow
from argparse import ArgumentParser


def main():
    parser = ArgumentParser()
    parser.add_argument('--disable', action='store_true', dest='quarantine',
                        help='disable the workers for 1000 years',
                        default=True)
    parser.add_argument('--enable', action='store_false', dest='quarantine',
                        help='enable the workers')
    parser.add_argument('-p', '--provisioner', required=True)
    parser.add_argument('-w', '--worker-type', required=True)
    parser.add_argument('-g', '--worker-group', required=True)
    parser.add_argument('workers', nargs='+', help='worker ids')

    args = parser.parse_args()

    if args.quarantine:
        quarantineUntil = fromNow('1000 years')
    else:
        quarantineUntil = fromNow('-1 hours')

    q = Queue()

    for worker_id in args.workers:
        res = q.quarantineWorker(args.provisioner, args. worker_type,
                                 args.worker_group, worker_id,
                                 payload={'quarantineUntil': quarantineUntil })
        if 'quarantineUntil' in res:
            print('{0[workerId]} quarantined until {0[quarantineUntil]}'.format(res))
        else:
            print('{0[workerId]} not quarantined'.format(res))


if __name__ == '__main__':
    main()
