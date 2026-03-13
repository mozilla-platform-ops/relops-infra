# Shell Scripts Audit

## Core Utilities

**`hostname_to_cart.sh`** — Converts a cart number (e.g. `023`) or hostname to its physical location: chassis number, cartridge slot, and ILO address. Handles the non-linear numbering quirks across mdc1/mdc2.

**`translate_ms_name.sh`** — Given a hostname or cart number, resolves the FQDN, the chassis web UI URL, and the `--hostname`/`--addr` flags needed by Expect scripts. Supports both Linux (`t-linux64-ms-`) and Windows (`t-w1064-ms-`) workers.

## Reimage Scripts

**`reimage_1804.sh`** — Reimages one or more cartridges on a chassis to Ubuntu 18.04 via `reimage_1804.exp`. Takes chassis number + node numbers, retries up to 3 times, logs output.

**`reimage_2404.sh`** — Same as above but for Ubuntu 24.04 via `reimage_2404.exp`.

**`reimage_loop.sh`** — Interactive wrapper: prompts for ILO and kickstart passwords, then calls `reimage_watch.exp` for each cart number passed as arguments.

## "Oneshot" (Reimage + Converge) Scripts

**`oneshot_linux.sh`** — The main workhorse. Given a chassis, cartridge, host number, Puppet role, and OS version, it: (1) acquires a per-chassis lock, (2) reimages via the appropriate `reimage_*.sh`, (3) waits for install to complete (14–18 min), (4) waits for SSH to come up, (5) delivers and runs the Ronin Puppet bootstrap script to converge the host.

**`oneshot_1804_x11_talos.sh`** — Convenience wrapper for `oneshot_linux.sh` with role `gecko_t_linux_talos` and OS `1804`. Supports `--confirm` flag; shows a dry-run by default.

**`oneshot_2404_x11_talos.sh`** — Same as above but for role `gecko_t_linux_2404_talos` and OS `2404`.

## Health Monitoring / Remediation

**`keep_moonshot_carts_up.sh`** — Long-running daemon (loops every 30 min by default). Finds all known cartridges, pings them, checks their last Taskcluster task, and power-cycles any that are unresponsive or stuck (no ping + task idle >30 min or in exception state). Reports metrics to InfluxDB via Telegraf.

**`reboot_hung.sh`** — One-shot older version of the above. Scans all 630 cart numbers across mdc1/mdc2, finds those not responding to ping, checks their last Taskcluster task, and power-cycles hung ones via `reboot_if_on.exp`.

**`reboot_loop.sh`** — One-liner: iterates over 7 chassis × 15 carts in mdc1, checks Taskcluster for each, and reboots any not found in the queue via `reboot.exp`.

## State/Status Queries

**`check_taskcluster_state.sh`** — Queries Taskcluster for the last task run on each Linux cartridge across all 14 chassis, prints state/timing, and logs to a timestamped file.

**`check_power.sh`** — Sends an ILO command (`show cartridge power all`) to one or more chassis hosts to report cartridge power states.

**`moon_command.sh`** — SSHes into all Linux moonshot workers and runs an arbitrary command (default: check Puppet last-run summary). Supports a parallelism argument.

## On-Host Maintenance

**`moonshot_cltbld_and_apt_systemd_unit/cltbld-and-apt-cleaner.sh`** — Runs on the worker itself (as root via systemd). If disk usage exceeds 70%, cleans up the `cltbld` user's build caches and runs `apt autoremove`/`apt clean`.
