"""
Read link capabilities from a physical sink's EDID.
"""

from __future__ import annotations

HF_OUI = bytes([0xD8, 0x5D, 0xC4])  # HDMI Forum, little-endian as stored in the EDID


def find_hf_vsdb(edid: bytes) -> bytes | None:
    """
    Find the HDMI Forum Vendor Specific Data Block in an EDID's CTA extensions.
    It carries the sink's link capabilities: max TMDS rate, SCDC/scrambling,
    max FRL rate, DSC and YCbCr 4:2:0 deep colour.

    An HF-SCDB (HDMI 2.1's alternative carrier for the same fields) is
    converted to an HF-VSDB.

    Returns:
        The whole data block, header byte included, or None if there isn't one
    """
    for start in range(128, len(edid) - 127, 128):
        block = edid[start:start + 128]
        if block[0] != 0x02:  # not a CTA-861 extension
            continue

        data_end = min(block[2], 127)
        i = 4
        while i < data_end:
            tag, length = block[i] >> 5, block[i] & 0x1F
            payload = block[i + 1:i + 1 + length]

            if tag == 3 and payload[:3] == HF_OUI:
                return bytes(block[i:i + 1 + length])

            # Extended tag 0x79: payload is ext tag, 2 reserved bytes, then the
            # same fields that follow the OUI in an HF-VSDB
            if tag == 7 and length >= 4 and payload[0] == 0x79:
                return bytes([0x60 | length]) + HF_OUI + bytes(payload[3:])

            i += 1 + length

    return None
