"""Talk to the ESC through a flight controller running INAV.

The second transport. The first, bridge.py, drives the wire directly from an
ESP32 and is what a bench session uses; this one is what a person with an
aeroplane has, because the only thing they need to own is the aircraft.

It needs no firmware support beyond what INAV already ships. `MSP_SET_PASSTHROUGH`
with MSP_PASSTHROUGH_SERIAL_FUNCTION_ID takes a serial function id, finds the port
configured for it, and hands the two ports to serialPassthrough(), which:

  * blocks in a while(1), so the scheduler stops and the flight controller's own
    SRXL2 driver cannot transmit over a programming session;
  * copies bytes raw in both directions, leaving the port in SERIAL_BIDIR - the
    single-wire half duplex the ESC expects is preserved;
  * mirrors the host's USB line coding onto that port every 15 ms, so changing
    the baud rate of this serial object changes the rate on the ESC's wire. The
    SRXL2 handshake can negotiate 115200 up to 400000 straight through it;
  * leaves on the Hayes escape, +++ after a second of silence, so a session ends
    without power-cycling the aircraft.

The one thing to know is that the reply is still on the wire: STM32 single-wire
half duplex does not mute the receiver while transmitting, so everything sent
comes back. That is handled here the same way the ESP32 handles it, by counting.
"""

import time

import serial

from . import Event

MSP_SET_PASSTHROUGH = 245
PASSTHROUGH_SERIAL_FUNCTION_ID = 0xFE

#: Serial function id for FUNCTION_ESC_SRXL2, i.e. the bit index in io/serial.h.
FUNCTION_ESC_SRXL2_ID = 29


def _msp_request(cmd, payload=b""):
    body = bytes([len(payload), cmd]) + payload
    crc = 0
    for b in body:
        crc ^= b
    return b"$M<" + body + bytes([crc])


class InavPassthrough:
    """Timestamps here are the host's, taken when bytes reached this process
    rather than when they reached the wire. Good enough to tell frames apart,
    not good enough to measure a turnaround - that is what the ESP32 is for."""

    def __init__(self, port, msp_baud=115200, wire_baud=115200, timeout=0.02):
        self.ser = serial.Serial(port, msp_baud, timeout=timeout)
        self._t0 = time.monotonic()
        self._echo_pending = 0
        self._report_echo = False
        self._open_passthrough(wire_baud)

    # --- setup -----------------------------------------------------------
    def _open_passthrough(self, wire_baud):
        self.ser.reset_input_buffer()
        self.ser.write(_msp_request(MSP_SET_PASSTHROUGH,
                                    bytes([PASSTHROUGH_SERIAL_FUNCTION_ID,
                                           FUNCTION_ESC_SRXL2_ID])))
        self.ser.flush()

        # $M> <len> <cmd> <data...> <crc>; one data byte, non-zero on success.
        deadline = time.monotonic() + 1.0
        buf = bytearray()
        while time.monotonic() < deadline:
            buf += self.ser.read(64)
            i = buf.find(b"$M>")
            if i >= 0 and len(buf) >= i + 6:
                if buf[i + 3] == 1 and buf[i + 5] == 0:
                    raise RuntimeError(
                        "the board has no port assigned to Spektrum Smart ESC - "
                        "assign one in the Ports tab and reboot")
                break
        else:
            raise RuntimeError("no reply to MSP_SET_PASSTHROUGH; is this an INAV port?")

        # From here the port is a raw pipe. Setting the rate here sets it on the
        # ESC's wire, because the flight controller mirrors the host line coding.
        self.set_baud(wire_baud)
        self.ser.reset_input_buffer()

    # --- the same surface as bridge.Bridge -------------------------------
    def _stamp(self):
        return int((time.monotonic() - self._t0) * 1e6)

    def set_baud(self, baud):
        self.ser.baudrate = baud
        time.sleep(0.05)            # the mirror is rate-limited to 15 ms

    def write(self, data):
        self._echo_pending += len(data)
        self.ser.write(bytes(data))
        self.ser.flush()

    def poll(self):
        chunk = self.ser.read(4096)
        if not chunk:
            return []

        events, stamp = [], self._stamp()
        if self._echo_pending:
            take = min(self._echo_pending, len(chunk))
            echo, chunk = chunk[:take], chunk[take:]
            self._echo_pending -= take
            if self._report_echo:
                events.append(Event(ord("E"), stamp, echo))
        if chunk:
            events.append(Event(ord("R"), stamp, chunk))
        return events

    def collect(self, seconds):
        events, end = [], time.monotonic() + seconds
        while time.monotonic() < end:
            events += self.poll()
            time.sleep(0.002)
        return events

    def report_echo(self, on=True):
        self._report_echo = bool(on)

    def keepalive(self, period_ms, frame=b""):
        """Not available on this transport.

        The ESP32 can repeat a frame on its own clock; here the host is the only
        clock there is, and USB scheduling is not one. Callers that need a cadence
        have to keep it themselves, and should not pretend otherwise.
        """
        raise NotImplementedError("keepalive needs the ESP32 bridge")

    def reset(self):
        self.ser.reset_input_buffer()
        self._echo_pending = 0

    def close(self):
        try:
            # Hayes escape: a second of silence, then +++. INAV wants the guard
            # interval before the first plus, or it is just three characters.
            time.sleep(1.1)
            self.ser.write(b"+++")
            self.ser.flush()
            time.sleep(0.3)
        finally:
            self.ser.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
