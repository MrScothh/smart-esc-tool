"""The two ways to reach the ESC's wire.

Both expose the same surface, so everything above them is written once:

    write(data)          put bytes on the wire
    poll()               -> [Event], whatever has arrived since the last call
    collect(seconds)     -> [Event], poll for a window
    set_baud(baud)       change the rate of the wire itself
    report_echo(on)      a single wire returns our own bytes; ask to see them
    reset()              drop what is in flight, forget the echo we are owed
    keepalive(ms, frame) repeat a frame, where the transport has its own clock
    close()

`esp32` drives the wire directly from a dev board and is what a bench session
uses: it owns the pad, the baud rate and a microsecond clock, so turnaround times
are measurable. `inav` goes through a flight controller's serial passthrough and
is what a person with an aeroplane has, because the only thing they need to own
is the aircraft. What is proven on the first runs unchanged on the second.
"""


class Event:
    """One report from a transport: a tag, a microsecond stamp, some bytes.

    Tags are `R` for bytes from the ESC, `E` for the echo of our own
    transmission, `I` for a message from the adapter itself and `!` for an error
    it raised.
    """

    def __init__(self, tag, stamp_us, data):
        self.tag, self.us, self.data = tag, stamp_us, data

    def __repr__(self):
        return "<%s %8d us %s>" % (chr(self.tag), self.us, self.data.hex(" "))


def open_transport(kind, port, **kwargs):
    if kind == "esp32":
        from .esp32 import Esp32Bridge
        return Esp32Bridge(port, **kwargs)
    if kind == "inav":
        from .inav import InavPassthrough
        return InavPassthrough(port, **kwargs)
    raise ValueError("unknown transport %r" % (kind,))
