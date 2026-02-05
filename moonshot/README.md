# moonshot scripts

Scripts for managing HP Moonshot chassis and cartridges.

## Utility Scripts

### hostname_to_cart.sh
Converts a hostname to chassis and cartridge numbers.
```bash
./hostname_to_cart.sh <hostname>
```

### check_power.sh
Checks the power status of moonshot cartridges.
```bash
./check_power.sh
```

### moon_command.sh / moon_ilo_command.exp
Executes generic commands on the Moonshot chassis iLO.
```bash
./moon_command.sh <chassis> <command>
```

## Reboot Scripts

### reboot_hung.sh
Reboots hung moonshot cartridges.
> The moonshot cartridges hang sometimes when rebooting. This script checks
if a cartridge is powered on but not responding to a ping. It then reboots those
cartridges.
```
./reboot_hung.sh
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

```bash
./oneshot_linux.sh <chassis> <cartridge> <host_number> <role> <os_version>
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
./oneshot_1804_x11_talos.sh <chassis> <cartridge> <host_number> --confirm
# Example: ./oneshot_1804_x11_talos.sh 1 3 023 --confirm

# Ubuntu 24.04 X11 Talos (actual execution)
./oneshot_2404_x11_talos.sh <chassis> <cartridge> <host_number> --confirm
# Example: ./oneshot_2404_x11_talos.sh 1 3 023 --confirm
```

### Configuration for Oneshot Scripts

**Environment Variables:**
- `RONIN_PUPPET_REPO_PATH` - Path to ronin_puppet repository (default: `$HOME/git/ronin_puppet`)
- `SKIP_REIMAGE` - (Optional) Set to skip the reimage step (useful for testing convergence only)


**Optional Override File**

If you'd like to have the newly imaged host use an override file immediately after initial convergence, place your override at `$RONIN_PUPPET_REPO_PATH/provisioners/linux/ronin_settings` the script will deploy it to the host.

**Optional Environment Variables**

If the override file has an effect too late in the process (e.g. you want to test initial convergence) you can set these variables and it will use the values in the bootstrap script.

- `ONESHOT_PUPPET_REPO` - Override default Puppet repository for bootstrap script
- `ONESHOT_PUPPET_BRANCH` - Override default Puppet branch for bootstrap script


## How It Works

**Reimage Workflow:**
1. `reimage_*.sh` wrapper scripts call their corresponding `reimage_*.exp` expect scripts
2. Expect scripts connect to Moonshot chassis iLO via SSH
3. Configure cartridge to PXE boot and power cycle
4. OS installation proceeds via netboot.xyz

**Oneshot Workflow:**
1. Wrapper scripts (`oneshot_*_x11_talos.sh`) call `oneshot_linux.sh` with pre-configured parameters
2. `oneshot_linux.sh` orchestrates the full workflow:
   - Reimage the host (calls appropriate `reimage_*.sh`)
   - Wait for OS installation to complete (10 minutes)
   - Poll for SSH connectivity
   - Upload and execute Puppet bootstrap script
   - Converge host with specified Puppet role
3. File locking ensures only one reimage per chassis runs at a time
