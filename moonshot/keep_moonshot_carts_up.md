# keep_moonshot_carts_up.sh

Monitoring and auto-remediation loop for Mozilla's Moonshot hardware workers.

## Setup

- Targets up to 630 moonshot cartridge workers matching hostname prefixes `t-linux64-ms-*` and `t-w1064-ms-*` across two datacenters (mdc1/mdc2)
- Prompts for ILO admin password and Telegraf credentials at startup
- Runs in a loop every 30 minutes (default)

## Each Iteration

1. **Discover hosts** — Resolves FQDNs for all cartridge slots by probing DNS across chassis/slot combinations

2. **Find non-pingable hosts** — Pings all known hosts in parallel (up to 16 at a time), collecting those that don't respond; skips hosts listed in `skip_hosts.txt`

3. **Check task queues** — Queries the Taskcluster API for pending task counts across all worker types under the `releng-hardware` provisioner

4. **Check last task status** — For each host, queries the Taskcluster API to find its most recent task and flags it for reboot if:
   - Last task ended >30 min ago AND there are queued tasks waiting, OR ended with an `exception` state
   - Last task started >120 min ago but never ended (hung/stalled)

5. **Reboot hung workers** — For hosts that are both non-pingable AND failed the task check, calls `up_carts_on_chassis.exp` via `hostname_to_cart.sh` to power-cycle them through the ILO interface

6. **Check chassis power state** — Checks each of the 14 chassis for cartridges that are powered off

7. **Report metrics** — Sends `noping`, `missing`, `hung`, and `down` events to a Telegraf/InfluxDB endpoint for monitoring dashboards

## Summary

Continuously watches for moonshot hardware workers that are unresponsive or stuck, and automatically power-cycles them via ILO to restore CI capacity.
