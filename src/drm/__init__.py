"""
DRM package — re-exports the public API for backward compatibility.
"""

from src.drm.sysfs import (
    find_global_active_display,
    get_card_name_from_device,
    get_drm_devices,
    run_command,
)

__all__ = [
    "find_global_active_display",
    "get_card_name_from_device",
    "get_drm_devices",
    "run_command",
]
