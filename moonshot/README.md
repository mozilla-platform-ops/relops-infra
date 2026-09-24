# moonshot scripts

Scripts for managing HP Moonshot chassis and cartridges.

## Setup

**SSH Key for HP iLO Access:**

Add the HP ILO SSH key (search `Relops Common Keys 2020-05-07` in Relops 1P) to ssh-agent to avoid password prompts during reimage operations:
```bash
ssh-add ~/.ssh/id_rsa_relops_2020-05-07
```

## Console Access

### ms_javaws_runner
Launches Java Web Start (javaws) with JNLP files for HP iLO remote console access.
> Python script that checks for existing Java processes, launches javaws, displays a progress spinner,
and cleans up the JNLP file on completion. Requires Python 3 and `uv`.

```bash
uv run ./ms_javaws_runner <jnlp_file>
```

## Name / Address Resolution

### hostname_to_cart.sh
Named oddly... really more like `cart_to_chassis.sh`.

Converts a cart number (e.g. `023`) or hostname to its physical location: chassis number, cartridge slot, and ILO address. Handles the non-linear numbering quirks across mdc1/mdc2.

```bash
./hostname_to_cart.sh hostname[s]/host_number[s]
```

### translate_ms_name.sh
Given a hostname or cart number, resolves the FQDN, the chassis web UI URL, and the `--hostname`/`--addr` flags needed by Expect scripts. Supports both Linux (`t-linux64-ms-`) and Windows (`t-w1064-ms-`) workers.

```bash
./translate_ms_name.sh <hostname_or_cart_number>
```

## Status & Monitoring

### check_taskcluster_state.sh
Queries Taskcluster for the last task run on each Linux cartridge across all 14 chassis, prints state/timing, and logs to a timestamped file.

```bash
./check_taskcluster_state.sh
```

### check_power.sh

Checks the power status of moonshot cartridges by sending an ILO command (`show cartridge power all`) to one or more chassis hosts to report cartridge power states.

```bash
./check_power.sh
```

### check_power_supplies.py

Checks every reported power supply in each of the seven mdc1 Moonshot chassis over iLO SSH. It uses the `relops` iLO SSH key already loaded in `ssh-agent` and the legacy algorithms required by the chassis. Use `--host` to check only one chassis.

```bash
./check_power_supplies.py
./check_power_supplies.py --host 6
```

### moon_command.sh

Executes generic commands on the Moonshot chassis iLO.

SSHes into all Linux moonshot workers and runs an arbitrary command (default: check Puppet last-run summary). Supports a parallelism argument.

```bash
./moon_command.sh <chassis> <command>
```

### keep_moonshot_carts_up.sh
Long-running daemon (loops every 30 min by default). Finds all known cartridges, pings them, checks their last Taskcluster task, and power-cycles any that are unresponsive or stuck (no ping + task idle >30 min or in exception state). Reports metrics to InfluxDB via Telegraf.

```bash
./keep_moonshot_carts_up.sh [hostname_prefix] [repeat_seconds] [ilo_user]
```

## Reboot / Remediation

### reboot_hung.sh

One-shot older version of `keep_moonshot_carts_up.sh`. Scans all 630 cart numbers across mdc1/mdc2, finds those not responding to ping, checks their last Taskcluster task, and power-cycles hung ones via `reboot_if_on.exp`.

Reboots hung moonshot cartridges.
> The moonshot cartridges hang sometimes when rebooting. This script checks
if a cartridge is powered on but not responding to a ping. It then reboots those
cartridges.

``` bash
./reboot_hung.sh
# enter Administrator password
```

### reboot_loop.sh
One-liner script: iterates over 7 chassis × 15 carts in mdc1, checks Taskcluster for each, and reboots any not found in the queue via `reboot.exp`.
```bash
./reboot_loop.sh
# enter Administrator password
```

## Reimage Scripts

### reimage_1804.sh / reimage_2404.sh
Reimages a moonshot cartridge with Ubuntu 18.04 or 24.04.
> These scripts use expect automation to connect to the Moonshot chassis iLO (HP Lights-Out),
configure PXE boot, power cycle the cartridge, and initiate OS installation via netboot.xyz.
Each script calls its corresponding `.exp` expect script to handle the interactive iLO session.

```bash
# Reimage with Ubuntu 18.04
./reimage_1804.sh <chassis> <cartridge>
# Example: ./reimage_1804.sh 1 3

# Reimage with Ubuntu 24.04
./reimage_2404.sh <chassis> <cartridge>
# Example: ./reimage_2404.sh 1 3
```

### reimage_loop.sh
Interactive wrapper: prompts for ILO and kickstart passwords, then calls `reimage_watch.exp` for each cart number passed as arguments.

```bash
./reimage_loop.sh <cart_number> [cart_number ...]
# enter ILO and kickstart passwords when prompted
```

---

## Oneshot Scripts

**Prerequisites for oneshot/reimage scripts:**
- `expect` - Automated iLO interaction
- `flock` - Serializing reimage operations
- `ssh` / `nc` - Remote host access and connectivity checks

### oneshot_linux.sh

Generic orchestration script for complete reimage and Puppet convergence workflow.
> Handles the full lifecycle: reimage → wait for OS installation → verify SSH connectivity →
run Puppet bootstrap → converge host. Uses file locking to serialize reimage operations per chassis.

Given a chassis, cartridge, host number, Puppet role, and OS version, it: (1) acquires a per-chassis lock, (2) reimages via the appropriate `reimage_*.sh`, (3) waits for install to complete (14–18 min), (4) waits for SSH to come up, (5) delivers and runs the Ronin Puppet bootstrap script to converge the host.

```bash
./oneshot_linux.sh <chassis> <cartridge> <host_number> <role> <os_version> [--ronin-settings <path>]
# Example: ./oneshot_linux.sh 1 3 023 gecko_t_linux_2404_talos 2404
```

### oneshot_1804_x11_talos.sh / oneshot_2404_x11_talos.sh
Wrapper scripts for reimaging and converging Talos test hosts.
> Convenience wrappers that call `oneshot_linux.sh` with pre-configured Puppet roles
for the specific OS version. Requires `--confirm` flag to execute; without it, shows a dry run
of what would happen.

```bash
# Dry run (shows what would happen)
./oneshot_1804_x11_talos.sh <chassis> <cartridge> <host_number>

# Ubuntu 18.04 X11 Talos (actual execution)
# The wrapper derives chassis and cartridge from the worker number or hostname.
./oneshot_1804_x11_talos.sh <host_number_or_hostname> --confirm
# Example: ./oneshot_1804_x11_talos.sh 229 --confirm
# The original explicit form remains supported:
./oneshot_1804_x11_talos.sh <chassis> <cartridge> <host_number> --confirm

# Ubuntu 24.04 X11 Talos (actual execution)
# The wrapper derives chassis and cartridge from the worker number or hostname.
./oneshot_2404_x11_talos.sh <host_number_or_hostname> --confirm
# Example: ./oneshot_2404_x11_talos.sh 229 --confirm
# Full hostnames work too:
./oneshot_2404_x11_talos.sh t-linux64-ms-229.test.releng.mdc1.mozilla.com --confirm
# The original explicit form remains supported:
./oneshot_2404_x11_talos.sh <chassis> <cartridge> <host_number> --confirm
# Supply a settings file from outside the Ronin Puppet checkout:
./oneshot_2404_x11_talos.sh 229 --ronin-settings ~/overrides/ronin_settings --confirm
```

### oneshot_2404_netperf.sh

Reimages and converges Ubuntu 24.04 Netperf hosts using the
`gecko_t_linux_2404_netperf` Puppet role. Accepts a worker number, hostname,
or explicit chassis/cartridge/worker number, like the Talos wrappers above.

```bash
# Dry run
./oneshot_2404_netperf.sh <host_number_or_hostname>

# Reimage and converge
./oneshot_2404_netperf.sh <host_number_or_hostname> --confirm
# Explicit form:
./oneshot_2404_netperf.sh <chassis> <cartridge> <host_number> --confirm
# Optional settings file:
./oneshot_2404_netperf.sh 229 --ronin-settings ~/overrides/ronin_settings --confirm
```

### Configuration for Oneshot Scripts

**Local Ronin Puppet checkout**

Oneshot uses `$HOME/git/ronin_puppet`, as set by `RONIN_PUPPET_REPO_PATH` near the top of `oneshot_linux.sh`. It reads two files from that checkout:

- `provisioners/linux/deliver_linux.sh` runs locally to prepare the host.
- `provisioners/linux/bootstrap_linux.sh` is copied to the host as `/tmp/bootstrap.sh` and run there.

The delivery script also reads `$HOME/vault.yaml`, which is outside the checkout. The bootstrap script fetches the Puppet code on the host from the configured repository and branch (by default, `mozilla-platform-ops/ronin_puppet` on `master`). Local changes to Puppet manifests or modules in the checkout are not used for convergence; local changes to the two scripts above are.

**Pause between imaging and convergence**

Set `SKIP_CONVERGE=1` to reimage a host, wait for SSH access, then stop before delivering the Puppet role, vault file, settings file, or bootstrap script. After your manual work, use `SKIP_REIMAGE=1` to skip the reimage step while still delivering and running bootstrap. The delivery script requires a host without an existing Puppet role:

```bash
SKIP_CONVERGE=1 ./oneshot_2404_x11_talos.sh 229 --confirm
# Perform manual work on the host.
SKIP_REIMAGE=1 ./oneshot_2404_x11_talos.sh 229 --confirm
```

The second command prompts for confirmation again and checks SSH before delivery. `SKIP_CONVERGE` also works with `--ronin-settings`, but the settings file is only sent during the convergence run.

**Optional Override File**

Pass `--ronin-settings <path>` to any oneshot wrapper (or to `oneshot_linux.sh`) to deploy that file to the host before bootstrap. The file can live outside the Ronin Puppet checkout. Without this option, oneshot does not deploy a settings file, even if one exists in the checkout.

**Optional Bootstrap Environment Variables**

If the override file has an effect too late in the process (e.g. you want to test initial convergence) you can set these variables and it will use the values in the bootstrap script.

- `ONESHOT_PUPPET_REPO` - Override default Puppet repository for bootstrap script
- `ONESHOT_PUPPET_BRANCH` - Override default Puppet branch for bootstrap script


## On-Host Maintenance

### moonshot_cltbld_and_apt_systemd_unit/cltbld-and-apt-cleaner.sh
Runs on the worker itself (as root via systemd). If disk usage exceeds 70%, cleans up the `cltbld` user's build caches and runs `apt autoremove`/`apt clean`.

## How It Works

**Reimage Workflow:**
1. `reimage_*.sh` wrapper scripts call their corresponding `reimage_*.exp` expect scripts
2. Expect scripts connect to Moonshot chassis iLO via SSH
3. Configure cartridge to PXE boot and power cycle
4. OS installation proceeds via netboot.xyz

**Oneshot Workflow:**
1. Wrapper scripts (`oneshot_*_x11_talos.sh` and `oneshot_2404_netperf.sh`) call `oneshot_linux.sh` with pre-configured parameters
2. `oneshot_linux.sh` orchestrates the full workflow:
   - Reimage the host (calls appropriate `reimage_*.sh`)
   - Wait for OS installation to complete (14–18 minutes)
   - Poll for SSH connectivity
   - Upload and execute Puppet bootstrap script
   - Converge host with specified Puppet role
3. File locking ensures only one reimage per chassis runs at a time
