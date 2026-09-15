"""Find the board by asking it, not by trusting the driver's label.

A USB serial port tells you which bridge chip it has and nothing about what is
behind it. An STM32 virtual port is a flight controller, a Betaflight board and
a 3D printer alike; a CP210x is an ESP32 here and a radio programming cable in
the next drawer. So the vendor and product ids are used only to decide what to
try first, and every candidate is then asked a question only the right device
can answer:

    a flight controller     MSP_FC_VARIANT, which replies with four characters
    the ESP32 adapter       its own identify command, which replies with a line

Both questions are harmless to the wrong device: MSP to a board that does not
speak it produces silence, and the adapter's command is a byte no flight
controller acts on.

Ports are probed in parallel because opening one that is busy or asleep can take
the better part of a second, and a person with a flight controller, an adapter
and a Bluetooth port should not wait for all three in turn.
"""

import socket
import threading
import time

import serial
from serial.tools import list_ports

MSP_API_VERSION = 1
MSP_FC_VARIANT = 2
MSP_FC_VERSION = 3

# Only ever used for ordering, never as an answer.
LIKELY_FC = {(0x0483, 0x5740),      # STM32 virtual com port, the usual board
             (0x0483, 0xDF11),      # STM32 in DFU, worth naming rather than probing
             (0x1209, 0x2020)}
LIKELY_BRIDGE = {(0x10C4, 0xEA60),  # CP210x
                 (0x1A86, 0x7523),  # CH340
                 (0x1A86, 0x55D4),
                 (0x303A, 0x1001)}  # ESP32-S3 native USB

#: INAV's SITL puts its first UART on this port, and MSP with it. A flight
#: controller that is a process on this machine answers the same questions as
#: one on a board, which is the whole point of looking here.
SITL_PORTS = (5760, 5761, 5762)


class Found(object):
    """One port and what answered on it."""

    def __init__(self, device, kind, detail, description=""):
        self.device = device
        self.kind = kind                # "inav", "betaflight", "esp32", None
        self.detail = detail
        self.description = description

    @property
    def usable(self):
        return self.kind in ("inav", "esp32")

    def label(self):
        if self.kind == "inav" and ":" in self.device:
            return "%s  -  %s" % (self.device, self.detail)
        if self.kind == "esp32":
            return "%s  -  SRXL2 adapter" % self.device
        if self.kind in ("inav", "betaflight"):
            return "%s  -  %s" % (self.device, self.detail)
        return "%s  -  %s" % (self.device, self.description or "nothing answered")

    def __repr__(self):
        return "<Found %s %s>" % (self.device, self.kind)


def _msp_frame(cmd, payload=b""):
    body = bytes([len(payload), cmd]) + payload
    crc = 0
    for b in body:
        crc ^= b
    return b"$M<" + body + bytes([crc])


def _msp_ask(ser, cmd, timeout=0.6):
    """Send one MSP request and return its payload, or None."""
    ser.reset_input_buffer()
    ser.write(_msp_frame(cmd))
    ser.flush()
    deadline = time.monotonic() + timeout
    buf = bytearray()
    while time.monotonic() < deadline:
        chunk = ser.read(64)
        if chunk:
            buf += chunk
            i = buf.find(b"$M>")
            if i >= 0 and len(buf) >= i + 5:
                length = buf[i + 3]
                if len(buf) >= i + 5 + length:
                    return bytes(buf[i + 5:i + 5 + length])
        else:
            time.sleep(0.01)
    return None


def _probe_flight_controller(device):
    try:
        with serial.Serial(device, 115200, timeout=0.1) as ser:
            time.sleep(0.15)            # some boards drop the first bytes
            variant = _msp_ask(ser, MSP_FC_VARIANT)
            if not variant:
                return None
            name = variant.decode("ascii", "replace").strip()
            version = _msp_ask(ser, MSP_FC_VERSION) or b""
            if len(version) >= 3:
                name += " %d.%d.%d" % (version[0], version[1], version[2])
            kind = "inav" if variant[:4] == b"INAV" else "betaflight"
            return Found(device, kind, name)
    except Exception:
        return None


def _probe_sitl(port, host="127.0.0.1"):
    try:
        sock = socket.create_connection((host, port), timeout=0.4)
    except OSError:
        return None
    try:
        sock.settimeout(0.8)
        sock.sendall(_msp_frame(MSP_FC_VARIANT))
        deadline = time.monotonic() + 1.0
        buf = bytearray()
        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(256)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            i = buf.find(b"$M>")
            if i >= 0 and len(buf) >= i + 5:
                length = buf[i + 3]
                if len(buf) >= i + 5 + length:
                    name = bytes(buf[i + 5:i + 5 + length]).decode(
                        "ascii", "replace").strip()
                    if not name:
                        return None
                    return Found("%s:%d" % (host, port), "inav",
                                 "%s in SITL" % name)
    except OSError:
        return None
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return None


def _probe_adapter(device):
    try:
        from .transport.esp32 import Esp32Bridge
    except Exception:
        return None
    try:
        br = Esp32Bridge(device)
    except Exception:
        return None
    try:
        br.reset()
        br.identify()
        deadline = time.monotonic() + 1.2
        while time.monotonic() < deadline:
            for event in br.poll():
                if event.tag == ord("I") and event.data:
                    text = event.data.decode("ascii", "replace").strip()
                    if "srxl2" in text.lower() or "bridge" in text.lower():
                        return Found(device, "esp32", text)
            time.sleep(0.02)
    except Exception:
        return None
    finally:
        try:
            br.close()
        except Exception:
            pass
    return None


def probe(device, prefer=None):
    """Ask one port what it is. `prefer` only decides which question comes first."""
    order = [_probe_flight_controller, _probe_adapter]
    if prefer == "esp32":
        order.reverse()
    for ask in order:
        found = ask(device)
        if found is not None:
            return found
    return None


def scan(timeout=6.0):
    """Every serial port, probed at once. Usable ones first.

    Returns a list of Found, including the ports that answered nothing - a
    person whose board is missing needs to see that it was looked at.
    """
    ports = list(list_ports.comports())
    results = {}
    extra = []
    threads = []

    def look_for_sitl():
        for port in SITL_PORTS:
            found = _probe_sitl(port)
            if found is not None:
                extra.append(found)
                return

    def work(p):
        ids = (p.vid, p.pid)
        prefer = "esp32" if ids in LIKELY_BRIDGE else None
        found = probe(p.device, prefer)
        results[p.device] = found or Found(p.device, None, "", p.description or "")

    for p in ports:
        t = threading.Thread(target=work, args=(p,))
        t.daemon = True
        t.start()
        threads.append(t)
    t = threading.Thread(target=look_for_sitl)
    t.daemon = True
    t.start()
    threads.append(t)
    end = time.monotonic() + timeout
    for t in threads:
        t.join(max(0.1, end - time.monotonic()))

    out = list(extra)
    for p in ports:
        out.append(results.get(p.device)
                   or Found(p.device, None, "", p.description or ""))
    out.sort(key=lambda f: (0 if f.kind == "inav" else
                            1 if f.kind == "esp32" else
                            2 if f.kind else 3, f.device))
    return out


def pick(found):
    """The one to use without asking: a flight controller, else an adapter."""
    for kind in ("inav", "esp32"):
        for f in found:
            if f.kind == kind:
                return f
    return None
