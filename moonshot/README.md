# moonshot scripts

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

## Oneshot Scripts

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
