# Moonshot Medic

Moonshot Medic (`bin/moonshot_medic.py`) is an automated triage tool for hung
`t-linux64-ms-*` Moonshot cartridges in Mozilla's Firefox CI fleet. It sits
at the intersection of detection, remediation, and evidence collection: when
a cartridge stops accepting work, the tool resets it via iLO, collects a
hang diagnostic report while the system is freshly rebooted, and records the
outcome in persistent state so repeat offenders can be identified and escalated.

## Role in the Flakiness Investigation

The Moonshot fleet suffers from a class of intermittent hangs where cartridges
become unresponsive to Taskcluster — jobs queue up, workers go silent, and
human operators have to intervene manually. Moonshot Medic exists to remove
that toil and, more importantly, to build a body of evidence about *which*
machines hang, *how often*, and *what state they were in when they did*.

The loop is:

1. **Detect** — [fleetroll](https://github.com/mozilla-relops/fleetroll_mvp)
   identifies hosts that haven't reported a successful task recently. Medic
   polls fleetroll on a configurable interval (default 15 min) and fetches
   the current candidates using `fleetroll host monitor` with `--once --json`
   and `--filter-file configs/filters/moonshots-hung.yaml` (worker evidence schema v3).

2. **Gate** — Before acting, Medic checks Fleetroll's versioned JSON freshness
   report and requires both host observations and Taskcluster observations to
   be fresh (≥65% coverage within the loop interval). It also requires the
   expected schema and a clean failure list. Stale, malformed, or incomplete
   data means Fleetroll itself may be broken, so the run is skipped rather than
   resetting hosts that might be fine.

   Each automatic batch also requires a complete historical identity from a
   successful host configuration audit, including its actual override pool.
   The identity must be between four hours and seven days old, with no known
   contradictions or uncertainty. A failed latest SSH audit is allowed: a hung
   host cannot refresh its audit. `--max-identity-age-hours` changes the seven-day
   maximum; Fleetroll itself retains baselines without expiry.

   Medic runs `fleetroll gather tc --use-historical-identity` for the eligible hosts, then
   reads a new JSON snapshot. Both the successful TC observation and latest
   lookup must match all four identity fields and the exact historical provenance.
   The lookup must have started during this check, remain younger than the
   configured freshness requirement (and under one hour), and link to the retained
   successful observation. Activity must be known and more than four hours old
   at query start, with no running or pending task. Active override workers,
   missing workers, failed lookups, unverified override changes, and malformed
   evidence are skipped with a reason; none prove inactivity.

   The check runs before any automatic host action and again after clock triage,
   immediately before iLO reset. Preview mode uses the same evidence rules and
   refreshes TC observations without changing hosts. Named-host/manual runs retain
   their explicit operator selection.

3. **Repair clock or reset** — Before resetting a selected bad host, Medic checks
   its OS and UTC clock over authenticated SSH. Only confirmed Ubuntu 18.04 hosts
   with clock skew greater than 5 minutes are remediated using
   `install_clock_fix.py`. The tool installs/enables the boot-time NTP sync service
   and syncs immediately, including when the service already exists. Medic verifies
   the clock afterward; a successful repair avoids the iLO reset and proceeds to
   diagnostics. Other OS versions and unknown OS responses never invoke the clock
   fix. Probe or repair failures fall back to normal reset triage.

   Each remaining bad host is cold-reset via iLO (`reset_moonshot.py`). Medic
   then waits for SSH port 22 to come back up to confirm the cartridge actually
   recovered. Hosts that don't come back are flagged as reset failures.
   The clock check also runs before collection, including after reboot and with
   `--no-reset`. Failed clock repairs mark the host's run as failed while still
   collecting diagnostics. The latest repair outcome and successful repair count
   are saved in `state.json` as `last_clock_fix` and `total_clock_fixes`, separately
   from reset counts. Existing freshness, inventory, circuit-breaker, and recency
   gates still apply; this does not add a separate fleet-wide scan.

4. **Collect** — Once a host is back online, `moonshot_hang_report.py` is
   uploaded and executed remotely via SSH. It captures kernel logs, process
   state, memory pressure, and other hang indicators into a timestamped
   Markdown report saved under `moonshot_debugging_results/YYYYMMDD/`.
   A `fleetroll gather host` audit is appended to the same file.

5. **Track** — Outcomes are persisted to `moonshot_debugging_results/state.json`.
   After 3 consecutive reset failures on the same host, Medic stops touching
   it for 6 hours and flags it in the overview as needing human attention.
   This prevents the tool from spinning on a truly broken cartridge.

   Reset, clock-fix, and diagnostics outcomes have separate `action_counts`
   (success/failure), `last_actions`, and a 30-day `action_history` per host.
   Each completed attempt also emits a JSON `medic_action` event into the daily
   run log with its host, action, timestamp, and outcome. Clock events include
   the original skew, verified skew when available, and duration; diagnostics
   events include duration and the report path on success. Run summaries count
   each action separately. Clock correction does not claim the worker has
   registered with Taskcluster: startup may still wait for Puppet's next
   10-minute retry, or require starting a failed Puppet service.

   The Markdown and HTML overviews show clock and diagnostics attempts,
   successes, failures, and latest outcomes separately from reset history.
   Reset columns explicitly describe resets; daily file counts describe
   diagnostics collected. New action counters cover outcomes recorded since
   this tracking was introduced. Existing reset counts retain their meaning,
   and older state remains readable without backfilling unknown attempts.

6. **Report** — After each run, `OVERVIEW.md` and `OVERVIEW.html` are
   regenerated. The HTML report shows daily collection counts, hosts that need
   human attention, and full per-host reset history with sortable columns and
   a UTC/local time toggle.

## Usage

### One-shot (named hosts)

```
./bin/moonshot_medic.py ms025 ms107
```

Resets and collects from the named hosts. Short labels (`ms025`) or FQDNs both work.

### Automated loop

```
./bin/moonshot_medic.py --auto --loop-interval 30 --confirm
```

Polls fleetroll every 30 minutes and processes whatever bad hosts it finds.
`--confirm` is required as a safeguard against accidental automation.

Automatic selection is also protected by a fleet-wide circuit breaker. If the
raw, deduplicated candidate list exceeds 20% of configured Moonshot inventory,
Medic resets nothing and exits so an operator can investigate. Recency filtering
happens after this check, preventing a systemic bad classification from slowly
resetting the fleet across multiple loops.

For an exceptional, attended recovery, raise the limit in a single-iteration run:

```
./bin/moonshot_medic.py --auto --once --confirm --max-fleet-reset-pct 25
```

Limits above the 20% default require `--once`, so an elevated blast radius
cannot remain active in the long-running daemon.

### Skip reset (host already rebooted)

```
./bin/moonshot_medic.py --no-reset ms025
```

Skips the iLO reset and goes straight to collection. Useful when a host was
manually rebooted or just came back from maintenance.

### Diagnose reachable workers (temporary, read-only)

```
uv run moonshot_medic.py --auto --diagnose-workers --no-voice
```

This is a separate, one-shot diagnosis path while zombie-aware gwhc v1 is being
deployed. It never repairs clocks, resets hosts, collects the normal hang report,
or updates recovery counters. Do not combine it with `--confirm` or named hosts.
`--no-reset` is not needed. Existing automatic recovery behavior is unchanged.

Candidates have old TC activity and no Fleetroll note, without requiring old
observation data. Medic requires configured inventory membership, a successful
host audit within the freshness requirement, a trusted worker identity, and a new
exact-identity TC lookup confirming activity over four hours old. Running/pending
tasks, unknown state for an identified task, identity contradictions, failed
lookups, recent processing, and failure backoff defer diagnosis. The normal
four-hour minimum identity age is bypassed only in this diagnosis path; fleet
freshness gates and the maximum identity age still apply.

`--diagnosis-limit` bounds candidates submitted for fresh TC checks and live probes
(default 10). This limit is separate from the reset circuit breaker because these
candidates never enter recovery. Deferred hosts and their reasons appear in the
report; rerun after reviewing them or adjust the limit for an attended scan.

Medic first runs `fleetroll host diagnose HOST --json`, with a 45-second timeout
and a 256 KiB report limit. It accepts completed Linux diagnoses for the requested
host in Fleetroll envelope version 1, with collection and gwhc timestamps within
the live check window (60 seconds of clock tolerance). A recognized normalized
`zombie.status` takes precedence over the fallback probe: `confirmed` produces
`worker_state: zombie`; `absent` produces `worker_state: not_zombie`, which does
not establish worker presence or task health. These are Medic's expected status
values for rollout; Fleetroll currently only documents `unknown`. Verify those
names against the agreed upstream contract before relying on rollout activation.
Medic does not classify zombies from legacy nested check data or aggregate health.

Unavailable, failed, unsupported, stale, or inconclusive Fleetroll diagnoses use
the existing SSH probe automatically, without a configuration switch. Structured
Fleetroll checks (including nested data) and selected envelope fields are retained
under `report` for a definitive diagnosis, or `fleetroll.report` alongside the
fallback result. Raw upstream reports, stderr, and exception text are omitted.

The fallback SSH probe uses `sudo -n python3` to read `/proc` process names and
states, PID/parent PID, boot ID, uptime, the operator hold, and the worker-status
file modification timestamp. It does not read process arguments, worker
configuration, or status-file contents. Each SSH command has a 45-second timeout
and accepts at most 32 matching processes. Remote errors are reported as
unverified without copying remote stderr into the report.

Results distinguish `zombie`, `live`, `startup`, `absent`, `held`, and `unverified`.
A `Z` process confirms a zombie, even beneath a live parent. A live process does
not establish task health. An absent process is `startup` below
`--startup-grace-minutes` (default 15); after that it remains `absent`, since this
probe does not establish whether Puppet is still provisioning. Neither diagnosis
authorizes recovery. Holds and incomplete process inspections take precedence.

The command emits `medic_worker_diagnosis` JSON events and saves a separate
`YYYYMMDD/*-worker-diagnosis.json` report under `moonshot_debugging_results/`.
Selection reasons, refreshed TC evidence for queried hosts, and live diagnosis
are separate fields. A successful exit means the report was produced, including
any deferred or unverified hosts; it does not mean every worker is healthy.
Fleet freshness or top-level collection/report failures exit nonzero.

The normalized diagnosis records `source: fleetroll_host_diagnose` or
`source: medic_proc_v1`, according to the evidence used. Automatic recovery of
reachable zombies remains disabled pending an attended validation.

### Key flags

| Flag | Default | Purpose |
|---|---|---|
| `--auto` | off | Pull bad-host list from fleetroll |
| `--loop-interval N` | 15 min | Sleep between auto runs |
| `--once` | off | Run one auto-mode iteration and exit |
| `--max-fleet-reset-pct N` | 20 | Maximum percentage of configured fleet selected for automatic reset; values above 20 require `--once` |
| `--freshness-requirement N` | same as loop-interval | Max acceptable fleetroll data age |
| `--freshness-min-pct N` | 65 | Minimum host and Taskcluster freshness coverage |
| `--max-identity-age-hours N` | 168 | Maximum historical worker identity age for automatic recovery |
| `--no-reset` / `-n` | off | Skip iLO reboot |
| `--ignore-recency` | off | Re-process hosts collected in the last 60 min |
| `--no-voice` / `-q` | off | Suppress macOS `say` announcements |
| `--voice-all-hours` | off | Speak outside 10:00–18:00 local time |

## Output

```
moonshot_debugging_results/
  state.json                    # Persistent per-host reset/failure history
  OVERVIEW.md                   # Human-readable summary table
  OVERVIEW.html                 # Sortable, interactive HTML report
  YYYYMMDD/
    run.log                     # Full timestamped log for the day's runs
    YYYYMMDDTHHMMSSz-ms025.md   # Per-host hang report + fleetroll audit
    ...
```

## Dependencies

Medic calls into two sibling repositories that must be checked out locally:

- `~/git/fleetroll_mvp` — provides `fleetroll data freshness`, `fleetroll gather host`,
  schema v3 `fleetroll host monitor --json`, and `fleetroll gather tc --use-historical-identity`
- `~/git/relops-infra/moonshot` — provides `reset_moonshot.py` (iLO reboot logic)

SSH access to the cartridges is required, as is network access to the iLO
management interfaces.

## Agent Analysis Prompt

Paste the following into a Claude conversation after pointing it at the
`moonshot_debugging_results/` directory:

---

```
You are helping investigate intermittent hangs in Mozilla's Moonshot CI fleet —
a rack of HP Moonshot cartridges running t-linux64-ms-* Linux workers for
Firefox CI. The fleet has ~280 hosts across two datacenters (mdc1, mdc2).
Cartridges are physically grouped into chassis of 45 slots each; mdc1 holds
chassis 1–7, mdc2 holds chassis 8+. Host numbering encodes the chassis slot
(e.g. ms001–ms045 are chassis 1, ms046–ms090 are chassis 2, etc., with some
DC layout exceptions around ms300 and ms615–ms630).

When a cartridge hangs, it is cold-reset via iLO and a hang diagnostic report
is collected immediately after reboot. Reports are stored as Markdown files in
moonshot_debugging_results/YYYYMMDD/. Each file contains kernel logs, process
state, memory pressure metrics, and a fleetroll host audit. Persistent outcome
data (total resets, consecutive failures, skip status) lives in
moonshot_debugging_results/state.json.

Please read state.json and as many of the per-host report files as you need,
then produce a structured analysis covering:

1. **Repeat offenders** — Which hosts hang most frequently? Are any in a
   skip/escalation state? Is the failure rate accelerating or stable?

2. **Chassis and DC clustering** — Do failures cluster within specific chassis
   (groups of 45 adjacent host numbers)? Do mdc1 and mdc2 differ in failure
   rate? Clustering suggests shared hardware (power, cooling, fabric) as a
   cause.

3. **Reset failure vs hang pattern** — Distinguish hosts that hang but recover
   cleanly after a reset from hosts that fail to come back after reset at all.
   The latter likely have a hardware fault rather than a software hang.

4. **Diagnostic signal in the reports** — Scan the hang reports for common
   patterns: OOM/memory pressure events, kernel panics or BUG traces, hung
   task warnings, specific processes or services that are stuck, filesystem or
   storage errors, time-of-day or uptime correlations.

5. **Hypotheses** — Based on the above, propose ranked hypotheses for the root
   cause(s). Be specific: e.g. "chassis 3 may have a power or cooling issue",
   "hosts with >X GB memory pressure are more likely to hang", "the hang
   appears correlated with a specific kernel version or package".

6. **Recommended next steps** — What additional data would confirm or rule out
   the top hypothesis? Are there hosts that should be taken out of rotation for
   physical inspection? Are there Puppet/config changes worth trying fleet-wide?

Be concrete and data-driven. Cite specific host labels (e.g. ms025), chassis
numbers, dates, and report excerpts where relevant. If the data is insufficient
to support a conclusion, say so rather than speculating.
```
