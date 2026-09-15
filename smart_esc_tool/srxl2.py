"""SRXL2 framing, mirrored from src/main/io/motor_srxl2.c.

Deliberately a second implementation rather than a wrapper around the firmware:
when the ESC disagrees with us on the bench, two independent readings of the
specification narrow down whether the fault is in the reading or in the code.
"""

MAGIC = 0xA6

# Packet types, specification section 7.
HANDSHAKE   = 0x21
BIND_INFO   = 0x41
PARAM_CFG   = 0x50
SIGNAL_QUAL = 0x55
TELEMETRY   = 0x80
CONTROL     = 0xCD

# Control Data commands.
CMD_CHANNEL_DATA  = 0x00
CMD_FAILSAFE_DATA = 0x01

# Device IDs.
OUR_DEVICE_ID  = 0x31   # flight controller type, unit 1 - same as the firmware
ESC_ID_FIRST   = 0x40
ESC_ID_LAST    = 0x4F
BROADCAST      = 0xFF

BAUD_LOW  = 115200
BAUD_HIGH = 400000
BAUD_BIT_400K = 0x01

# X-Bus telemetry sensor IDs. An Avian relays more than its own: with the ESC
# frames come Smart Battery ones, which carry a different structure entirely and
# decode as nonsense if taken for ESC data - rpm in the hundreds of thousands.
# The firmware checks this byte before decoding; so must anything else.
TELEM_SENSOR_ESC       = 0x20
TELEM_SENSOR_SMART_BAT = 0x42

TELEM_SENSOR_NAMES = {TELEM_SENSOR_ESC: "ESC", TELEM_SENSOR_SMART_BAT: "SmartBattery"}

THROTTLE_CHANNEL = 0     # CH1, per Spektrum: "the ESC will always look at CH 1"

PACKET_NAMES = {
    HANDSHAKE: "Handshake", BIND_INFO: "BindInfo", PARAM_CFG: "ParamConfig",
    SIGNAL_QUAL: "SignalQuality", TELEMETRY: "Telemetry", CONTROL: "ControlData",
}


def crc16(data, crc=0):
    """CRC-16/CCITT, poly 0x1021, init 0, no reflection - common/crc.c."""
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def seal(buf):
    """Patch the length byte and append the CRC, big-endian on the wire."""
    buf = bytearray(buf) + b"\x00\x00"
    buf[2] = len(buf)
    c = crc16(buf[:-2])
    buf[-2] = c >> 8
    buf[-1] = c & 0xFF
    return bytes(buf)


def crc_ok(frame):
    return len(frame) >= 5 and crc16(frame) == 0   # trailing CRC makes the whole frame zero


def us_to_value(us):
    """us = 988 + (value >> 6), inverted. 1500 us lands on 0x8000, "Servo Center"."""
    v = (int(us) - 988) << 6
    return max(0, min(0xFFFC, v)) & 0xFFFC


def value_to_us(value):
    return 988 + (value >> 6)


def handshake(dest, baud_bits=0, source=OUR_DEVICE_ID, priority=10, uid=0x494E4156):
    return seal(bytes([MAGIC, HANDSHAKE, 0, source, dest, priority, baud_bits, 0])
                + uid.to_bytes(4, "little"))


def control_data(channels, reply_id=0x00, rssi=100, losses=0, command=CMD_CHANNEL_DATA):
    """channels: {index0based: value}. Values go out in mask-bit order, little-endian.

    Channels that are not in the dict are not sent at all rather than centred:
    on an aircraft ESC 1500 us is half throttle, so a wrong guess about which
    index carries throttle should leave the motor idle, not spinning.
    """
    mask = 0
    for i in channels:
        mask |= 1 << i
    body = bytearray([MAGIC, CONTROL, 0, command, reply_id,
                      rssi, losses & 0xFF, (losses >> 8) & 0xFF])
    body += mask.to_bytes(4, "little")
    for i in sorted(channels):
        body += int(channels[i]).to_bytes(2, "little")
    return seal(body)


def param_query(dest, param_id, source=OUR_DEVICE_ID):
    """Section 7.4. Request 0x50 queries, 0x57 writes.

    Table 2 gives the length as 14 and section 7.4 as 15; Spektrum's own
    SrxlParamPacket is 15 bytes, so the table is what is wrong. The reply format
    is never documented anywhere - finding out what actually comes back is the
    whole reason for the bench session.
    """
    return seal(bytes([MAGIC, PARAM_CFG, 0, 0x50, dest])
                + int(param_id).to_bytes(4, "little")
                + (0).to_bytes(4, "little"))


def param_write(dest, param_id, value, source=OUR_DEVICE_ID):
    return seal(bytes([MAGIC, PARAM_CFG, 0, 0x57, dest])
                + int(param_id).to_bytes(4, "little")
                + int(value).to_bytes(4, "little"))


def telemetry_poll(dest):
    """A Control Data frame that sends no channels and only asks for a reply."""
    return control_data({}, reply_id=dest)


def split(stream):
    """Pull whole frames out of a byte stream.

    Returns (frames, remainder). Resyncs on the magic byte, which is what a
    single wire needs: the first bytes after a turnaround are routinely the tail
    of something we were not listening for.
    """
    frames, i = [], 0
    while True:
        while i < len(stream) and stream[i] != MAGIC:
            i += 1
        if i + 3 > len(stream):
            return frames, stream[i:]
        length = stream[i + 2]
        if length < 5 or length > 80:
            i += 1                      # not a plausible header, keep scanning
            continue
        if i + length > len(stream):
            return frames, stream[i:]
        frames.append(stream[i:i + length])
        i += length


def decode_esc_telemetry(payload):
    """STRU_TELE_ESC, big-endian. 0xFFFF and 0xFF mean "no data"."""
    def be16(o):
        return (payload[o] << 8) | payload[o + 1]

    rpm, volts, tfet, curr, tbec = be16(2), be16(4), be16(6), be16(8), be16(10)
    return {
        "sensor_id":   payload[0],
        "rpm":         None if rpm == 0xFFFF else rpm * 10,
        "voltage_V":   None if volts == 0xFFFF else volts / 100.0,
        "temp_fet_C":  None if tfet == 0xFFFF else tfet / 10.0,
        "current_A":   None if curr == 0xFFFF else curr / 100.0,
        "temp_bec_C":  None if tbec == 0xFFFF else tbec / 10.0,
        "current_bec": None if payload[12] == 0xFF else payload[12] / 10.0,
        "volts_bec":   None if payload[13] == 0xFF else payload[13] / 20.0,
        "throttle_pc": None if payload[14] == 0xFF else payload[14] / 2.0,
        "power_pc":    None if payload[15] == 0xFF else payload[15] / 2.0,
    }


def describe(frame):
    """One line per frame, for the log."""
    if len(frame) < 5:
        return "runt %s" % frame.hex(" ")
    kind = PACKET_NAMES.get(frame[1], "type 0x%02X" % frame[1])
    ok = "" if crc_ok(frame) else "  BAD CRC"
    body = frame[3:-2]

    if frame[1] == HANDSHAKE and len(body) >= 8:
        return ("Handshake  src=0x%02X dst=0x%02X prio=%d baud=0x%02X info=0x%02X "
                "uid=0x%08X%s" % (body[0], body[1], body[2], body[3], body[4],
                                  int.from_bytes(body[5:9], "little"), ok))
    if frame[1] == TELEMETRY and len(body) >= 17:
        sensor = body[1]
        if sensor != TELEM_SENSOR_ESC:
            return "Telemetry  dst=0x%02X sensor=0x%02X %s  %s%s" % (
                body[0], sensor, TELEM_SENSOR_NAMES.get(sensor, "not decoded"),
                body[2:].hex(" "), ok)
        t = decode_esc_telemetry(body[1:])
        return "Telemetry  dst=0x%02X sensor=0x%02X %s%s" % (
            body[0], t["sensor_id"],
            " ".join("%s=%s" % (k, v) for k, v in t.items()
                     if k != "sensor_id" and v is not None), ok)
    if frame[1] == PARAM_CFG:
        return "ParamConfig  %s%s" % (body.hex(" "), ok)
    return "%-14s %s%s" % (kind, body.hex(" "), ok)
