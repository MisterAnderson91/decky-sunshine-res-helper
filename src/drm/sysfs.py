"""
Sysfs and debugfs helpers for discovering GPU devices, display ports,
and connector state.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def run_command(command: str) -> subprocess.CompletedProcess[str]:
    """Run a shell command and return the CompletedProcess."""
    return subprocess.run(command, shell=True, capture_output=True, text=True)


def get_drm_devices() -> list[Path]:
    """Get list of DRM devices from /sys/kernel/debug/dri/"""
    debug_dri_path = "/sys/kernel/debug/dri"
    devices: list[Path] = []

    result = run_command(f"ls -1 {debug_dri_path}")
    if result.returncode != 0:
        print(
            "Error: /sys/kernel/debug/dri not found or not accessible. Make sure debugfs is mounted."
        )
        return devices

    for line in result.stdout.strip().split("\n"):
        if line.startswith("0000:"):
            devices.append(Path(debug_dri_path) / line)

    return sorted(devices)



def find_global_active_display() -> tuple[Path | None, str | None, str | None]:
    """
    Searches all DRM devices and returns the first one that is actively rendering (enabled).
    Returns (drm_device_path, card_name, port_name).
    """
    drm_devices = get_drm_devices()
    
    # Pass 1: Look for a display that is both 'connected' and 'enabled' (CRTC bound)
    for dev in drm_devices:
        card_name = get_card_name_from_device(dev)
        drm_path = Path("/sys/class/drm")
        for display in drm_path.iterdir():
            if display.name.startswith(f"{card_name}-"):
                status_file = display / "status"
                enabled_file = display / "enabled"
                if status_file.exists() and enabled_file.exists():
                    try:
                        if status_file.read_text().strip() == "connected" and enabled_file.read_text().strip() == "enabled":
                            port_name = display.name.replace(f"{card_name}-", "")
                            return dev, card_name, port_name
                    except Exception:
                        pass
                        
    # Pass 2: Fallback to just 'connected' if no display is actively enabled (e.g. asleep)
    for dev in drm_devices:
        card_name = get_card_name_from_device(dev)
        drm_path = Path("/sys/class/drm")
        for display in drm_path.iterdir():
            if display.name.startswith(f"{card_name}-"):
                status_file = display / "status"
                if status_file.exists():
                    try:
                        if status_file.read_text().strip() == "connected":
                            port_name = display.name.replace(f"{card_name}-", "")
                            return dev, card_name, port_name
                    except Exception:
                        pass
                        
    return None, None, None


def get_card_name_from_device(drm_device_path: Path) -> str:
    """Extract card name (e.g., 'card1') from DRM device path."""
    device_name = drm_device_path.name

    drm_class_path = Path("/sys/class/drm")
    for card_dir in drm_class_path.iterdir():
        if card_dir.name.startswith("card") and "-" not in card_dir.name:
            device_link = card_dir / "device"
            if device_link.exists():
                try:
                    target = os.readlink(device_link)
                    if device_name in target:
                        return card_dir.name
                except Exception:
                    pass

    # Fallback: assume card1 for discrete GPU (most common case)
    return "card1"
