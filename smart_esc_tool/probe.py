"""Bench sequence for an Avian, over either transport.

    smart-esc-tool COM5 selftest     - wire disconnected, checks the adapter
    smart-esc-tool COM5 listen       - power the ESC while this runs
    smart-esc-tool COM5 find         - poll 0x40..0x4F for a device
    smart-esc-tool COM5 link 0x40    - handshake, negotiate 400000, hold it
    smart-esc-tool COM5 telem 0x40   - poll telemetry and decode it
    smart-esc-tool COM5 param 0x40   - the undocumented 0x50 reply
    smart-esc-tool COM5 config        - open the ESC's own menu and read it
    smart-esc-tool COM5 set "BRAKE TYPE=Reverse"
    smart-esc-tool COM5 menu          - scroll the whole list, not just the window
    smart-esc-tool COM5 save "BRAKE TYPE=Reverse"   - change it and keep it
    smart-esc-tool COM5 reset         - restore the ESC's defaults

Add --via inav to go through a flight controller running INAV instead of the
ESP32 adapter; the port is then the board's, and it needs a port assigned to
Spektrum Smart ESC. Everything above the transport is identical, which is the
point of writing it this way: what is proven on the bench is the same code a
person with only an aeroplane will run.

Each step is separate on purpose: the answers feed the next one, and every one
of them is a claim in iNavFlight/inav#11947 that is currently read from the
specification rather than measured.
"""

import sys
import time

from . import srxl2
from .transport import open_transport


def _log(events, prefix="    "):
    for e in events:
        if e.tag == ord("R"):
            frames, rest = srxl2.split(e.data)
            for f in frames:
                print("%s%8d us  %s" % (prefix, e.us, srxl2.describe(f)))
            if rest:
                print("%s%8d us  partial %s" % (prefix, e.us, rest.hex(" ")))
        elif e.tag == ord("E"):
            print("%secho     %s" % (prefix, e.data.hex(" ")))
        elif e.tag == ord("I"):
            print("%sbridge   %s" % (prefix, e.data.decode("ascii", "replace")))
        elif e.tag == ord("!"):
            print("%sERROR    %s" % (prefix, e.data.decode("ascii", "replace")))


def _frames(events):
    stream = b"".join(e.data for e in events if e.tag == ord("R"))
    return srxl2.split(stream)[0]


def _hold(br, frame, period_ms, seconds, prefix="    "):
    """Repeat a frame for a while, on whichever clock the transport has.

    The ESP32 has one of its own and keeps the cadence exactly. Through a flight
    controller the only clock is this process and USB scheduling sits in the
    middle, so the interval is a request rather than a promise - fine for a
    keepalive the ESC only checks against a 250 ms timeout, not fine for
    measuring anything.
    """
    try:
        br.keepalive(period_ms, frame)
        _log(br.collect(seconds), prefix)
        br.keepalive(0)
    except NotImplementedError:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            br.write(frame)
            _log(br.collect(period_ms / 1000.0), prefix)


def selftest(br):
    """Nothing connected to the pin. Proves the adapter before we blame the ESC.

    One pad carrying both directions means every transmission is received back.
    If that echo is missing or corrupt the GPIO matrix is not set up, and no
    amount of staring at an ESC that will not answer would ever reveal it.
    """
    br.report_echo(True)
    br.identify()
    _log(br.collect(0.3))

    probe = srxl2.handshake(0x40, srxl2.BAUD_BIT_400K)
    print("  sending  %s" % probe.hex(" "))
    br.write(probe)
    events = br.collect(0.3)
    _log(events)

    got = b"".join(e.data for e in events if e.tag == ord("E"))
    if got == probe:
        print("  OK       the pad reads back exactly what it drove")
    elif not got:
        print("  FAIL     no echo: transmit and receive are not on the same pad")
    else:
        print("  FAIL     echo differs, got %s" % got.hex(" "))
    return got == probe


def listen(br, seconds=6.0):
    """An ESC whose unit ID is 0 announces itself, unprompted, for 200 ms after
    reset - specification 7.2.1, every 50 ms. Connect the pack while this runs;
    it is the cheapest way to learn the device ID and to see the ESC's own idea
    of the framing before we have said anything at all."""
    print("  listening at 115200 - connect the battery now (no propeller)")
    br.reset()
    br.report_echo(False)
    events = br.collect(seconds)
    _log(events)
    if not _frames(events):
        print("  nothing heard: either the unit ID is not 0, or the wire is wrong")


def find(br):
    """Poll each ESC ID in turn. An ESC with a non-zero unit ID never announces
    itself, so this is the only way to find it."""
    br.reset()
    for dev in range(srxl2.ESC_ID_FIRST, srxl2.ESC_ID_LAST + 1):
        br.write(srxl2.handshake(dev, srxl2.BAUD_BIT_400K))
        events = br.collect(0.08)
        frames = _frames(events)
        if frames:
            print("  0x%02X answers:" % dev)
            _log(events, "      ")
            return dev
        print("  0x%02X silent" % dev)
    return None


def link(br, dev, hold=5.0):
    """Handshake, then the broadcast that tells the bus to move to 400000.

    The switch is the first thing on this branch that is read from the
    specification and never measured. Watch for the link surviving it: frames
    still arriving after the rate change means the ESC followed, garbage means
    it did not and the driver should stay at 115200.
    """
    br.reset()
    br.report_echo(False)

    print("  handshake to 0x%02X at 115200" % dev)
    br.write(srxl2.handshake(dev, srxl2.BAUD_BIT_400K))
    events = br.collect(0.2)
    _log(events)

    reply = _frames(events)
    supported = reply[0][6] if reply and len(reply[0]) > 6 else 0
    print("  ESC advertises baud bits 0x%02X" % supported)
    if not supported & srxl2.BAUD_BIT_400K:
        print("  it does not claim 400000 - staying at 115200")
        return

    print("  broadcast: everyone to 400000")
    br.write(srxl2.handshake(srxl2.BROADCAST, srxl2.BAUD_BIT_400K))
    time.sleep(0.05)                     # let the frame clear the shift register
    br.set_baud(srxl2.BAUD_HIGH)

    idle = srxl2.control_data({srxl2.THROTTLE_CHANNEL: srxl2.us_to_value(1000)})
    print("  holding idle throttle at 400000 for %.0f s" % hold)
    _hold(br, idle, 20, hold)            # 50 Hz, the firmware's cadence


def telem(br, dev, seconds=3.0):
    """Ask for telemetry and decode it against what motor_srxl2.c expects."""
    br.reset()
    idle = srxl2.control_data({srxl2.THROTTLE_CHANNEL: srxl2.us_to_value(1000)},
                              reply_id=dev)
    _hold(br, idle, 100, seconds)        # 10 Hz, the driver's default rate


def param(br, dev, first=0, count=8):
    """The undocumented half of section 7.4.

    The request format is specified; the reply is not, anywhere. Whether an
    Avian answers this at all decides between the SRXL2 route to ESC settings
    and the TextGen one, so it is the single most informative frame on the list.
    """
    br.reset()
    br.report_echo(False)
    for pid in range(first, first + count):
        frame = srxl2.param_query(dev, pid)
        br.write(frame)
        events = br.collect(0.15)
        frames = _frames(events)
        print("  param %2d  %s" % (pid, "no reply" if not frames else ""))
        _log(events, "      ")


def config(br, dev=None, assignments=(), finish=None):
    """Open the ESC's on-screen menu, read it, and optionally change it.

    The menu is the ESC's own: names, current values and a cursor, sent as
    telemetry. Reading a value back is something stick programming cannot do,
    so this is the only way for a tool to report what an ESC is actually set
    to rather than what it was last told.
    """
    from .avian_menu import AvianMenu, MenuError

    br.set_baud(srxl2.BAUD_LOW)
    br.reset()
    br.report_echo(False)

    menu = AvianMenu(br, dev, log=lambda m: print("    %s" % m))
    menu.connect()
    menu.enter()
    print("    %s" % (menu.screen.title() or "menu"))
    for name, value, selected in menu.settings():
        print("    %s %-14s %s" % (">" if selected else " ", name, value))

    for text in assignments:
        if "=" not in text:
            print("    not an assignment: %r" % text)
            continue
        name, _, value = text.partition("=")
        try:
            got = menu.set(name, value)
            print("    %s -> %s" % (name.strip(), got))
        except MenuError as exc:
            print("    %s" % exc)

    if assignments:
        print("    after:")
        for name, value, selected in menu.settings():
            print("    %s %-14s %s" % (">" if selected else " ", name, value))

    if finish:
        # Without this the ESC forgets everything on the next power cycle: the
        # values change on screen immediately, but only this entry writes them.
        menu.activate(finish)
        print("    %s" % finish)
    return menu


def walk(br, dev=None):
    """Scroll the cursor to the end, listing every parameter it passes.

    The screen is a five-line window onto a longer list, so what a single read
    shows is not what the ESC offers. Whatever ends the list - a save entry, an
    exit, or simply the last parameter - is only visible from down there.
    """
    from .avian_menu import AvianMenu

    br.set_baud(srxl2.BAUD_LOW)
    br.reset()
    br.report_echo(False)

    menu = AvianMenu(br, dev, log=lambda m: print("    %s" % m))
    menu.connect()
    menu.enter()
    found = menu.walk()
    print("    %d entries:" % len(found))
    for i, (name, value) in enumerate(found, 1):
        print("    %2d  %-16s %s" % (i, name, value))
    print("    screen at the end:")
    for line in menu.screen.render().splitlines():
        print("      %s" % line)
    return menu


def main():
    argv = [a for a in sys.argv[1:] if a != "--via"]
    via = "inav" if "--via" in sys.argv and "inav" in argv else "esp32"
    argv = [a for a in argv if a not in ("inav", "esp32")]

    if len(argv) < 2:
        print(__doc__)
        return 1
    port, step = argv[0], argv[1]
    dev = srxl2.ESC_ID_FIRST
    if len(argv) > 2 and step not in ("set", "save"):
        try:
            dev = int(argv[2], 0)
        except ValueError:
            pass

    with open_transport(via, port) as br:
        print("== %s (via %s) ==" % (step, via))
        if step == "selftest":
            if via == "inav":
                print("  selftest is for the ESP32 adapter; there is nothing to")
                print("  check here that the passthrough handshake has not already")
                print("  answered.")
                return 1
            selftest(br)
        elif step == "listen":
            listen(br)
        elif step == "find":
            found = find(br)
            print("  device: %s" % ("0x%02X" % found if found else "none"))
        elif step == "link":
            link(br, dev)
        elif step == "telem":
            telem(br, dev)
        elif step == "param":
            param(br, dev)
        elif step == "config":
            config(br)
        elif step == "menu":
            walk(br)
        elif step == "save":
            config(br, assignments=argv[2:], finish="EXIT W/ SAVE")
        elif step == "reset":
            config(br, finish="DEFAULT/EXIT")
        elif step == "set":
            config(br, assignments=argv[2:])
        else:
            print(__doc__)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
