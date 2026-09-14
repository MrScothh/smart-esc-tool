"""Host side of the ESP32 SRXL2 wire adapter.

The bridge owns the pad, the baud rate and the timestamps; everything about
SRXL2 itself lives here, so a wrong guess costs an edit and not a flash.
"""

import time

import serial

from . import Event

SLIP_END, SLIP_ESC, SLIP_ESC_END, SLIP_ESC_ESC = 0xC0, 0xDB, 0xDC, 0xDD


def _slip(payload):
    out = bytearray([SLIP_END])
    for b in payload:
        if b == SLIP_END:
            out += bytes([SLIP_ESC, SLIP_ESC_END])
        elif b == SLIP_ESC:
            out += bytes([SLIP_ESC, SLIP_ESC_ESC])
        else:
            out.append(b)
    out.append(SLIP_END)
    return bytes(out)


class Esp32Bridge:
    def __init__(self, port, usb_baud=921600, timeout=0.02):
        self.ser = serial.Serial(port, usb_baud, timeout=timeout)
        self._buf = bytearray()
        self._esc = False
        self._acc = bytearray()
        time.sleep(0.3)                 # boards that auto-reset on DTR
        self.ser.reset_input_buffer()

    # --- link ------------------------------------------------------------
    def _send(self, payload):
        self.ser.write(_slip(payload))
        self.ser.flush()

    def poll(self):
        """Everything the bridge has said since the last call."""
        chunk = self.ser.read(4096)
        events = []
        for b in chunk:
            if b == SLIP_END:
                if len(self._acc) >= 5:
                    events.append(Event(self._acc[0],
                                        int.from_bytes(self._acc[1:5], "little"),
                                        bytes(self._acc[5:])))
                self._acc.clear()
                self._esc = False
                continue
            if b == SLIP_ESC:
                self._esc = True
                continue
            if self._esc:
                b = SLIP_END if b == SLIP_ESC_END else SLIP_ESC if b == SLIP_ESC_ESC else b
                self._esc = False
            self._acc.append(b)
        return events

    def collect(self, seconds):
        """Poll for a fixed window and return the events, in order."""
        events, end = [], time.monotonic() + seconds
        while time.monotonic() < end:
            events += self.poll()
            time.sleep(0.002)
        return events

    # --- commands --------------------------------------------------------
    def identify(self):
        self._send(b"?")

    def set_baud(self, baud):
        self._send(b"B" + int(baud).to_bytes(4, "little"))

    def write(self, data):
        self._send(b"W" + bytes(data))

    def keepalive(self, period_ms, frame=b""):
        """Repeat a frame on the ESP32's own clock, or pass 0 to stop.

        For when the ESC needs a cadence kept up - it has its own receive
        timeout - while the host is between probes.
        """
        self._send(b"K" + int(period_ms).to_bytes(2, "little") + bytes(frame))

    def report_echo(self, on=True):
        """A single wire returns our own transmissions; this asks to see them.

        Worth turning on once at the start of a session: it is the only direct
        evidence of what the pad actually drove, as opposed to what we asked for.
        """
        self._send(b"E" + bytes([1 if on else 0]))

    def reset(self):
        """Drop everything in flight and forget the echo we are still owed."""
        self._send(b"X")
        time.sleep(0.05)
        self.poll()

    def close(self):
        try:
            self.keepalive(0)
        finally:
            self.ser.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
