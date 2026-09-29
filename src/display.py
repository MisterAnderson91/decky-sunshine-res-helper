"""
Connect and disconnect virtual displays by managing EDIDs and sysfs connector state.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from src.drm import (
    find_global_active_display,
    get_card_name_from_device,
    get_drm_devices,
    run_command,
)

log = logging.getLogger(__name__)
from src.edid import create_edid, find_best_vic_resolution, get_pixel_clock_info

SCRIPT_DIR = Path(__file__).parent.parent.absolute()

target_user = "deck"

def _get_target_uid() -> int:
    import pwd
    try:
        return pwd.getpwnam(target_user).pw_uid
    except KeyError:
        return 1000

def connect(width: int, height: int, refresh_rate: int, device: str | None = None) -> bool:
    """
    Connect a virtual display:
    1. Generate custom EDID
    2. Find empty display slot
    3. Override EDID
    4. Turn on virtual display
    """
    log.info(f"Applying display resolution override: {width}x{height}@{refresh_rate}Hz")

    state_file = SCRIPT_DIR / "virt_display.state"
    if state_file.exists():
        stale = state_file.read_text().strip().split("\n")
        stale_card = stale[0] if len(stale) > 0 else ""
        stale_port = stale[1] if len(stale) > 1 else ""
        stale_edid = stale[2] if len(stale) > 2 else ""
        if stale_card and stale_port:
            log.info(f"  Stale session detected ({stale_card}-{stale_port}) — cleaning up...")
            if stale_edid:
                _ = run_command(f"sh -c 'echo -n reset > {stale_edid}'")
            _ = run_command(f"sh -c 'echo detect > /sys/class/drm/{stale_card}-{stale_port}/status'")
            time.sleep(0.5)
        state_file.unlink()

    # Step 1: Generate custom EDID
    log.info("Step 1: Generating custom EDID...")
    pixel_clock_mhz, max_mhz, will_break = get_pixel_clock_info(width, height, refresh_rate)
    if will_break:
        vic_result = find_best_vic_resolution(width, height, refresh_rate)
        if vic_result:
            vic_width, vic_height, vic_refresh, vic_code, vic_name = vic_result
            width, height, refresh_rate = vic_width, vic_height, vic_refresh

    edid_data = create_edid(
        width=width,
        height=height,
        refresh_rate=refresh_rate,
        enable_hdr=True,
        display_name="Virtual Display",
    )
    edid_file = SCRIPT_DIR / "custom_edid.bin"
    _ = edid_file.write_bytes(edid_data)
    log.info(f"  ✓ Created EDID file: {edid_file}")

    # Step 2 & 3: Find actively rendering display
    log.info("Step 2 & 3: Finding actively rendering display...")
    drm_device, card_name, active_port = find_global_active_display()
    
    if not drm_device or not active_port:
        log.error("Error: No active display slots found across any GPU")
        return False
        
    log.info(f"  ✓ Selected actively rendering display: {active_port} on {card_name}")
    slot_device = drm_device

    # Step 4: Save original EDID
    log.info(f"Step 4: Saving original EDID for {active_port}...")
    original_edid_path = Path(f"/sys/class/drm/{card_name}-{active_port}/edid")
    backup_edid_file = SCRIPT_DIR / "original_edid.bin"
    fallback_edid_file = SCRIPT_DIR / "tv_edid.bin"
    
    if original_edid_path.exists():
        _ = run_command(f"sh -c 'cat {original_edid_path.absolute()} > {backup_edid_file.absolute()}'")
        
    if backup_edid_file.exists() and backup_edid_file.stat().st_size > 0:
        log.info(f"  ✓ Saved current hardware EDID to {backup_edid_file}")
    elif fallback_edid_file.exists() and fallback_edid_file.stat().st_size > 0:
        _ = run_command(f"sh -c 'cp {fallback_edid_file.absolute()} {backup_edid_file.absolute()}'")
        log.info(f"  ✓ Display asleep. Used persistent fallback TV EDID from {fallback_edid_file}")
    else:
        log.warning(f"  No original EDID found for {active_port} and no tv_edid.bin fallback exists!")

    # Step 5: Drop EDID cache by forcing disconnect
    log.info(f"Step 5: Forcing disconnect on ({active_port})...")
    status_path = f"/sys/class/drm/{card_name}-{active_port}/status"
    _ = run_command(f"sh -c 'echo off > {status_path}'")
    time.sleep(0.5)

    # Step 6: Override EDID and force ON
    log.info(f"Step 6: Overriding EDID and forcing {active_port} ON...")
    edid_override_path = slot_device / active_port / "edid_override"
    _ = run_command(f"sh -c 'cat {edid_file.absolute()} > {edid_override_path}'")
    _ = run_command(f"sh -c 'echo on > {status_path}'")
    time.sleep(0.5)
    _ = state_file.write_text(f"{card_name}\n{active_port}\n{edid_override_path}\n{backup_edid_file}\n")
    
    # Force Gamescope to rescan the backend to trigger the display switch
    log.info("Step 6: Triggering Gamescope backend rescan...")
    target_uid = _get_target_uid()
    _ = run_command(f"sudo -u {target_user} XDG_RUNTIME_DIR=/run/user/{target_uid} gamescopectl backend_set_dirty")
    
    log.info(f"✓ Display override successfully applied on {card_name}-{active_port}")
    return True


def disconnect() -> bool:
    """
    Disconnect virtual display:
    1. Turn off virtual display
    """
    log.info("Reverting display resolution override...")
    state_file = SCRIPT_DIR / "virt_display.state"
    if not state_file.exists():
        return False

    state_data = state_file.read_text().strip().split("\n")
    if len(state_data) < 2:
        return False

    card_name = state_data[0]
    virtual_port = state_data[1]
    
    status_path = f"/sys/class/drm/{card_name}-{virtual_port}/status"
    
    log.info(f"Step 1: Forcing disconnect on ({virtual_port})...")
    _ = run_command(f"sh -c 'echo off > {status_path}'")
    time.sleep(0.5)
    
    edid_override_path = state_data[2] if len(state_data) > 2 else ""
    backup_edid_file = state_data[3] if len(state_data) > 3 else ""
    
    if backup_edid_file and Path(backup_edid_file).exists():
        log.info(f"Step 2: Restoring original EDID to poison kernel cache on ({virtual_port})...")
        # Write original EDID back to override
        _ = run_command(f"sh -c 'cat {backup_edid_file} > {edid_override_path}'")
        # Force it ON so the kernel caches the original EDID
        _ = run_command(f"sh -c 'echo on > {status_path}'")
        time.sleep(0.5)
        Path(backup_edid_file).unlink()
    else:
        log.info(f"Step 2: No backup EDID to restore for ({virtual_port})")

    log.info(f"Step 3: Clearing EDID override and returning ({virtual_port}) to hardware polling...")
    if edid_override_path:
        _ = run_command(f"sh -c 'echo -n reset > {edid_override_path}'")
    _ = run_command(f"sh -c 'echo detect > {status_path}'")
    
    time.sleep(1.0)
    target_uid = _get_target_uid()
    _ = run_command(f"sudo -u {target_user} XDG_RUNTIME_DIR=/run/user/{target_uid} gamescopectl backend_set_dirty")

    state_file.unlink()
    log.info("✓ Display resolution reverted!")
    return True
