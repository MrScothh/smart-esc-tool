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

import socket
import struct
import time

import serial

from . import Event


class _Tcp(object):
    """A TCP socket wearing the few methods this module uses on a serial port.

    INAV's SITL exposes each of its UARTs as a TCP socket rather than a COM
    port, so the flight controller can be a process on this machine. Everything
    above the transport is then exercised for real - MSP, the passthrough
    handshake, the framing - against the same firmware that runs on a board.
    """

    def __init__(self, host, port, timeout=0.02):
        self.sock = socket.create_connection((host, int(port)), timeout=3.0)
        self.sock.settimeout(timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.baudrate = 0            # a socket has no line rate to set

    def read(self, size=1):
        try:
            return self.sock.recv(size)
        except socket.timeout:
            return b""
        except OSError:
            return b""

    def write(self, data):
        self.sock.sendall(bytes(data))
        return len(data)

    def flush(self):
        pass

    def reset_input_buffer(self):
        self.sock.setblocking(False)
        try:
            while self.sock.recv(65536):
                pass
        except Exception:
            pass
        finally:
            self.sock.settimeout(0.02)

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

MSP_SET_PASSTHROUGH = 245
PASSTHROUGH_SERIAL_FUNCTION_ID = 0xFE

#: Serial function id for FUNCTION_ESC_SRXL2, i.e. the bit index in io/serial.h.
FUNCTION_ESC_SRXL2_ID = 29


def _split_host_port(port):
    """("127.0.0.1", 5760) for a SITL address, None for a COM port."""
    text = str(port)
    if text.count(":") != 1:
        return None
    host, _, tail = text.partition(":")
    if not tail.isdigit() or not host:
        return None
    return host, int(tail)


def _msp_request(cmd, payload=b""):
    body = bytes([len(payload), cmd]) + payload
    crc = 0
    for b in body:
        crc ^= b
    return b"$M<" + body + bytes([crc])


#: INAV's report on its SRXL2 ESC link: calibration phase, then 1 if linked.
MSP2_INAV_ESC_SRXL2_STATUS = 0x2233


def _msp2_request(cmd, payload=b""):
    body = struct.pack("<BHH", 0, cmd, len(payload)) + payload
    crc = 0
    for b in body:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0xD5) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return b"$X<" + body + bytes([crc])


def esc_linked(port, wait=4.0, baud=115200):
    """After a session: does the flight controller have the ESC again?

    Leaving the ESC's menu restarts it, and it announces itself for less than a
    second; ending the passthrough takes INAV two seconds of silence. So whether
    the flight controller picks the ESC up afterwards is a question with a real
    answer either way, and the aircraft must not fly on a guess.

    True once INAV reports the link, False if it has not within `wait` seconds,
    None if the board never answered or does not know the command.
    """
    host_port = _split_host_port(port)
    link = _Tcp(host_port[0], host_port[1], 0.05) if host_port else \
        serial.Serial(port, baud, timeout=0.05)
    answered = False
    try:
        end = time.monotonic() + wait
        while time.monotonic() < end:
            link.reset_input_buffer()
            link.write(_msp2_request(MSP2_INAV_ESC_SRXL2_STATUS))
            link.flush()
            buf = bytearray()
            deadline = time.monotonic() + 0.3
            while time.monotonic() < deadline:
                buf += link.read(64)
                i = buf.find(b"$X>")
                if i >= 0 and len(buf) >= i + 10:
                    size = struct.unpack("<H", bytes(buf[i + 6:i + 8]))[0]
                    if size >= 2 and len(buf) >= i + 8 + size:
                        answered = True
                        if buf[i + 9]:
                            return True
                        break
                if buf.find(b"$X!") >= 0:
                    return None     # a firmware without the command
            time.sleep(0.2)
    finally:
        link.close()
    return False if answered else None


class InavPassthrough:
    """Timestamps here are the host's, taken when bytes reached this process
    rather than when they reached the wire. Good enough to tell frames apart,
    not good enough to measure a turnaround - that is what the ESP32 is for."""

    def __init__(self, port, msp_baud=115200, wire_baud=115200, timeout=0.02):
        host_port = _split_host_port(port)
        if host_port:
            self.ser = _Tcp(host_port[0], host_port[1], timeout)
            # A socket carries no echo: the single wire that returns our own
            # bytes is on the far side of whatever bridges the UART, and that
            # bridge is what decides. Counting echo here would eat real replies.
            self.echoes = False
        else:
            self.ser = serial.Serial(port, msp_baud, timeout=timeout)
            # Counting the echo was a byte-for-byte accounting of our own
            # transmission coming back off the single wire, and it only has to
            # slip once - a dropped byte, a reply that lands mid-count - for it
            # to start eating real frames instead. Nothing above needs it: the
            # frame splitter resyncs on the magic byte, and the menu ignores
            # handshakes that are not from an ESC and control data entirely.
            # So the echo is simply passed up, the way the wire delivers it.
            self.echoes = False
        self._t0 = time.monotonic()
        self._echo_pending = 0
        self._report_echo = False
        #: Whether setting the rate on this link sets it on the ESC's wire.
        #: A flight controller mirrors the host's line coding onto the port it
        #: is bridging, so it does. The ESP32 adapter does not: its USB rate is
        #: how the host reaches it at all, and the wire rate is its own.
        #: Deciding this wrongly is silent and total - the rate change lands on
        #: the wrong link and every byte after it is noise.
        self.mirrors_baud = True
        self._open_passthrough(wire_baud)

    # --- setup -----------------------------------------------------------
    #: Rates to try when talking MSP over a real serial port. A flight
    #: controller answers at the first; the ESP32 adapter, which can also answer
    #: as one, runs its USB side fast because it normally carries wire data
    #: wrapped in framing and timestamps. Trying rather than assuming costs one
    #: second in the worst case and removes a setting nobody should have to know.
    MSP_RATES = (115200, 921600)

    def _ask_passthrough(self):
        """Request passthrough once. True if a port was opened, False if refused,
        None if nothing answered at this rate."""
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
                return buf[i + 5] != 0
        return None

    def _open_passthrough(self, wire_baud):
        answer = self._ask_passthrough()
        if answer is None and not isinstance(self.ser, _Tcp):
            # Opening a USB serial port asserts DTR and RTS, and on a board
            # wired for auto-reset - every ESP32 development board, and some
            # flight controllers - that restarts it. Nothing answers for a
            # second or two afterwards, so a single silent attempt means very
            # little. Wait out a reset, then try every rate again.
            time.sleep(1.6)
            for _ in range(2):
                for rate in self.MSP_RATES:
                    if self.ser.baudrate != rate:
                        self.ser.baudrate = rate
                        time.sleep(0.15)
                    self.ser.reset_input_buffer()
                    answer = self._ask_passthrough()
                    if answer is not None:
                        break
                if answer is not None:
                    break

        if answer is None:
            raise RuntimeError("no reply to MSP_SET_PASSTHROUGH; is this an INAV port?")
        if answer is False:
            raise RuntimeError(
                "the board has no open port for Spektrum Smart ESC - assign one "
                "in the Ports tab, set the motor protocol to SRXL2, and reboot")

        # A link that answered above 115200 is not a flight controller's MSP
        # port; it is the adapter, whose USB rate is the link itself. Anything
        # that later asks for a wire rate must not be allowed to change it.
        if isinstance(self.ser, _Tcp) or self.ser.baudrate > 115200:
            self.mirrors_baud = False
        else:
            self.set_baud(wire_baud)
        self.ser.reset_input_buffer()

    # --- the same surface as bridge.Bridge -------------------------------
    def _stamp(self):
        return int((time.monotonic() - self._t0) * 1e6)

    def set_baud(self, baud):
        if not self.mirrors_baud:
            return                  # the far end owns the wire's rate
        self.ser.baudrate = baud
        time.sleep(0.05)            # the mirror is rate-limited to 15 ms

    def write(self, data):
        if self.echoes:
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
            # And a second of silence after it, which is when INAV actually
            # leaves: any byte before then, from here or from whoever opens the
            # port next, resets the escape and the board stays in passthrough.
            time.sleep(1.2)
        finally:
            self.ser.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
