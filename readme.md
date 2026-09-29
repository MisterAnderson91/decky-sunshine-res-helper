# Decky Sunshine Res-Helper

This tool is designed to work alongside [decky-sunshine](https://github.com/s0t7x/decky-sunshine) (the Sunshine plugin for Decky Loader). 
It modifies your active display's EDID on-the-fly to perfectly match the client's resolution and refresh rate when streaming via Sunshine on SteamOS/Gamescope systems.
It runs as a persistent daemon and automatically manages display connections by overriding EDID information and triggering Gamescope rescans.

> ⚠️ If your monitor or TV screen ever gets stuck on "Invalid format" or similar, just hard reboot your device and it will clear the override.

## Requirements

- Python 3
- `jeepney` Python package (installed automatically by `install.sh`)
- debugfs mounted at `/sys/kernel/debug/`
- systemd

## Installation

Clone the repo:

```bash
git clone https://github.com/MisterAnderson91/decky-sunshine-res-helper
cd decky-sunshine-res-helper
```

Run the install script as root:

```bash
sudo ./install.sh
```

This will:
1. Install `jeepney` via pip
2. Copy the project to `~/.local/share/decky-sunshine-res-helper`
3. Install and enable the `decky-sunshine-res-helper` systemd service

The daemon starts automatically at boot and restarts if it crashes. To check it is running:

```bash
systemctl status decky-sunshine-res-helper
journalctl -u decky-sunshine-res-helper -f
```

### Updating

Pull the latest changes and re-run the install script:

```bash
git pull
sudo ./install.sh
```

## Configure Sunshine

The daemon listens on a FIFO pipe at `~/.sunshine-res-helper.in`. Sunshine talks to it by echoing arguments to that file.

In Sunshine's **General** tab, set:

**Do Command (On Client Connect):**

```bash
sh -c "echo --connect,--width,${SUNSHINE_CLIENT_WIDTH},--height,${SUNSHINE_CLIENT_HEIGHT},--refresh-rate,${SUNSHINE_CLIENT_FPS} > /home/deck/.sunshine-res-helper.in && cat /home/deck/.sunshine-res-helper.out"
```

**Undo Command (On Client Disconnect):**

```bash
sh -c "echo --disconnect > /home/deck/.sunshine-res-helper.in && cat /home/deck/.sunshine-res-helper.out"
```



## How It Works

### On Connect

1. Daemon receives `--connect` with width, height, and refresh rate
2. Generates a custom EDID matching the client's display parameters
3. Finds the currently active display slot (e.g. your physical TV on `HDMI-A-1`).
4. Saves a backup of your physical TV's hardware EDID. If the TV is currently unresponsive (e.g. turned off or on another input), it falls back to a persistently saved `tv_edid.bin`.
5. Forces the active slot `off` via sysfs, clearing the kernel's immediate display state.
6. Overrides EDID for that slot via debugfs with the custom Client EDID.
7. Forces the slot `on` via sysfs, effectively injecting the Custom EDID into the kernel's hardware cache.
8. Triggers a Gamescope backend rescan using `gamescopectl backend_set_dirty`, causing Gamescope to gracefully transition the display to the new custom resolution.

### On Disconnect

1. Daemon receives `--disconnect`
2. Forces the slot `off` via sysfs to clear the display state.
3. Restores your physical TV's original EDID to the override file and forces the slot `on`, intentionally poisoning the kernel's hardware cache with your TV's real configuration.
4. Clears the EDID override file.
5. Sets the slot status back to hardware `detect` mode.
6. Triggers a Gamescope backend rescan using `gamescopectl backend_set_dirty`, which forces Gamescope to transition back to the physical display at its true native resolution.

### On Sunshine Crash or Stop

The daemon actively monitors Sunshine's logs and service status. If Sunshine crashes, stops, or a client network connection drops unexpectedly, the daemon automatically reverts the display after a 15-second timeout, instantly reapplying the custom resolution if the client reconnects.

### On System Sleep / Wake

The daemon holds a systemd sleep inhibitor lock so it can clean up before the system suspends. On sleep it restores the physical display; on wake it overrides the EDID again automatically if a session was active.

### On Shutdown

Both `PrepareForShutdown` (via DBus) and SIGTERM trigger a graceful disconnect before the process exits, so physical displays are restored even if Sunshine didn't send an undo command.

## Known Issues

- Everything appears small when a device with a Retina display connects
- On MacBooks with notches, the notch area cuts into content
- Very high resolutions and refresh rates may not work due to EDID 1.4 pixel-clock limits
- Stuttering on some displays: Enable V-Sync and frame pacing in Moonlight.


## Tested On

- SteamOS

## Credits

This project is based on [sunshine_virt_display](https://github.com/frostplexx/sunshine_virt_display) by frostplexx. While the original project focused on creating virtual displays across various compositors, this version has been rewritten and optimized specifically for SteamOS and Gamescope by dynamically overriding the active display's EDID.
