"""Bytes the Python implementation produces, for test/protocol.test.js to compare the JavaScript port against.

    python web/test/make_fixtures.py      (from the repository root; rewrites web/test/fixtures.json)
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from smart_esc_tool import srxl2                                            # noqa: E402
from smart_esc_tool.avian_menu import HIGH, LOW, MID                        # noqa: E402
from smart_esc_tool.discover import _msp_frame                              # noqa: E402
from smart_esc_tool.transport.esp32 import _slip                            # noqa: E402
from smart_esc_tool.transport.inav import _msp2_request, _msp_request       # noqa: E402

channels = dict((i, MID) for i in range(8))
channels[0] = LOW
channels[2] = HIGH
stream = (b"\x00\x13" + srxl2.handshake(0x40, 1) + b"\xa6\x01"
          + srxl2.control_data(channels, 0x40) + srxl2.handshake(0xFF)[:6])
frames, rest = srxl2.split(stream)

fixtures = {
    "us_to_value": {str(us): srxl2.us_to_value(us) for us in (900, 988, 1000, 1500, 2000, 2100)},
    "handshake_40_400k": srxl2.handshake(0x40, srxl2.BAUD_BIT_400K).hex(),
    "handshake_broadcast": srxl2.handshake(srxl2.BROADCAST, 0).hex(),
    "control_menu_40": srxl2.control_data(channels, 0x40).hex(),
    "control_empty": srxl2.control_data({}, 0x41).hex(),
    "stream": stream.hex(),
    "split_frames": [f.hex() for f in frames],
    "split_rest": rest.hex(),
    "esc_telemetry": srxl2.decode_esc_telemetry(bytes.fromhex("20 00 01 f4 0a 8c 01 2c 00 64 01 18 0a 64 c8 50")),
    "msp_passthrough": _msp_request(245, bytes([0xFE, 29])).hex(),
    "msp_fc_variant": _msp_frame(2).hex(),
    "msp2_srxl2_status": _msp2_request(0x2233).hex(),
    "slip": _slip(b"W\xc0\x01\xdb\x02").hex(),
}
with open(os.path.join(os.path.dirname(__file__), "fixtures.json"), "w", encoding="utf-8") as fh:
    json.dump(fixtures, fh, indent=2)
    fh.write("\n")
print("fixtures.json: %d entries" % len(fixtures))
