"""Driving an Avian's on-screen menu from a host, over either transport.

Spektrum expose this menu to a transmitter. Nothing about it is transmitter
specific: the ESC watches ordinary channel values and answers on ordinary
telemetry, so a host that can put frames on the wire can do the same thing - and
through a flight controller's serial passthrough, that host is a PC on the other
end of the aircraft's USB cable.

Everything below was measured on an `SPMXAE70C`, because Spektrum publish no
manual for the Smart Lite series at all:

    entering        throttle low, elevator up, aileron left, held ~8 s,
                    then aileron right, held ~8 s. The screen prompts for
                    each step in turn and says "Hold 5-10sec".
    elevator up     cursor to the next parameter
    elevator down   cursor to the previous one
    aileron right   next value        aileron left   previous value

A command is a pulse - a channel to one extreme for about a third of a second
and back to centre - because that is what a hand on a stick does. Holding an
extreme repeats or does nothing, depending on the ESC's mood, which is why this
module never holds one except when entering.

The value on screen updates as soon as it changes, so every action here is
verified by reading it back rather than assumed.
"""

import time

from . import srxl2
from .textgen import Screen, TEXTGEN_SENSOR

THROTTLE, AILERON, ELEVATOR = 0, 1, 2

LOW = srxl2.us_to_value(1000)
MID = srxl2.us_to_value(1500)
HIGH = srxl2.us_to_value(2000)

PULSE = 0.35            # how long a stick is held at an extreme
RECENTRE = 0.25         # and how long it rests in the middle afterwards
SETTLE = 0.8            # no row has changed for this long: the screen is stable
STEP_HOLD = 8.0         # the screen asks for five to ten seconds


class MenuError(Exception):
    pass


class AvianMenu(object):
    def __init__(self, bridge, device=None, log=None):
        self.br = bridge
        self.device = device
        self.screen = Screen()
        self.log = log or (lambda msg: None)
        self._sink = bytearray()
        self._last_change = 0.0
        self._channels = dict((i, MID) for i in range(8))
        self._channels[THROTTLE] = LOW
        self._probe = srxl2.ESC_ID_FIRST
        self._probing = True
        self._last_telemetry = 0.0

    # ------------------------------------------------------------------- wire
    def _pump(self, seconds):
        """Keep the link alive and absorb whatever comes back.

        The ESC drops the link after about a quarter of a second without a
        frame, so nothing in this module may block for longer than that.
        """
        end = time.monotonic() + seconds
        nxt = nxt_hs = 0.0
        while True:
            now = time.monotonic()
            if now >= end:
                return
            if self.device is not None and now >= nxt:
                nxt = now + 0.02
                # Telemetria a ogni frame: qui serve la pagina di testo il piu'
                # in fretta possibile, ed e' l'unica cosa che l'ESC risponde. Il
                # prezzo e' che a 50 Hz di richieste l'ESC tiene il collegamento
                # ma smette di ubbidire al gas - va bene per un menu, dove il
                # motore deve stare fermo, e non va bene in volo. Il driver di
                # INAV infatti non scende mai sotto un frame su due.
                #
                # Solo dopo che l'ESC si e' fatto riconoscere: un Avian che
                # sente control data da un master con cui non ha fatto
                # l'handshake tace, e smette anche di annunciarsi, che e'
                # proprio quello che connect() aspetta.
                self.br.write(srxl2.control_data(self._channels,
                                                 reply_id=self.device))
            if self.device is None and self._probing and now >= nxt_hs:
                nxt_hs = now + 0.1
                # Un Avian senza master si annuncia da solo, una ventina di
                # volte al secondo: succede dopo l'accensione e ogni volta che
                # smette di sentire la FC, come quando INAV apre il passthrough.
                # Chiamarlo copre il caso in cui tace lo stesso, e l'indirizzo
                # non e' noto in anticipo.
                self.br.write(srxl2.handshake(self._probe, srxl2.BAUD_BIT_400K))
                self._probe += 1
                if self._probe > srxl2.ESC_ID_LAST:
                    self._probe = srxl2.ESC_ID_FIRST
            for event in self.br.poll():
                if event.tag == ord("R") and event.data:
                    self._sink.extend(event.data)
            if len(self._sink) >= 5:
                frames, rest = srxl2.split(bytes(self._sink))
                self._sink = bytearray(rest)
                for frame in frames:
                    self._absorb(frame)
            time.sleep(0.002)

    def _absorb(self, frame):
        body = frame[3:-2]
        if frame[1] == srxl2.HANDSHAKE and self.device is None:
            if srxl2.ESC_ID_FIRST <= frame[3] <= srxl2.ESC_ID_LAST:
                self.device = frame[3]
                self.br.write(srxl2.handshake(self.device, srxl2.BAUD_BIT_400K))
                self.br.write(srxl2.handshake(srxl2.BROADCAST, 0))
                self.log("ESC 0x%02X" % self.device)
        elif frame[1] == srxl2.TELEMETRY and len(body) >= 4:
            self._last_telemetry = time.monotonic()
            if body[1] == TEXTGEN_SENSOR and self.screen.feed(body[1:]):
                self._last_change = time.monotonic()

    # ------------------------------------------------------------- primitives
    def connect(self, timeout=45.0):
        """Wait for an ESC to answer, saying so while it waits.

        An Avian with no master announces itself about twenty times a second:
        after power-up, and whenever the flight controller stops talking to it,
        which is what opening INAV's passthrough does. Nothing but probes goes
        out until one of those is heard, because control data from a master it
        has not shaken hands with silences it. The window is generous for the
        case where it is not powered yet and a person is reaching for a switch.
        """
        start = time.monotonic()
        end = start + timeout
        said = 0.0
        while self.device is None and time.monotonic() < end:
            self._pump(0.25)
            waited = time.monotonic() - start
            if waited - said >= 5.0:
                said = waited
                self.log("waiting for the ESC, %.0f s of %.0f"
                         % (waited, timeout))
        if self.device is None:
            raise MenuError("no ESC answered on 0x%02X..0x%02X after %.0f s: "
                            "check that it has power and that the signal wire "
                            "reaches the right pad"
                            % (srxl2.ESC_ID_FIRST, srxl2.ESC_ID_LAST, timeout))
        return self.device

    def settle(self, quiet=SETTLE, limit=6.0):
        """Wait for the screen to stop changing, then return it."""
        end = time.monotonic() + limit
        while time.monotonic() < end:
            self._pump(0.15)
            if (self.screen.complete()
                    and time.monotonic() - self._last_change > quiet):
                break
        return self.screen

    def pulse(self, channel, value, settle=True):
        self._channels[channel] = value
        self._pump(PULSE)
        self._channels[channel] = MID
        self._pump(RECENTRE)
        if settle:
            self.settle()

    def next_entry(self):
        self.pulse(ELEVATOR, HIGH)

    def previous_entry(self):
        self.pulse(ELEVATOR, LOW)

    def next_value(self):
        self.pulse(AILERON, HIGH)

    def previous_value(self):
        self.pulse(AILERON, LOW)

    # ------------------------------------------------------------------ entry
    def enter(self, hold=STEP_HOLD):
        """The two-step stick hold that opens the menu.

        The screen narrates it: step one asks for left aileron, step two for
        right, both with the throttle down and the elevator up. It is worth
        following the screen rather than a fixed script, because an ESC already
        in the menu must not be sent through the sequence again.
        """
        self.connect()
        self.settle(quiet=0.4, limit=4.0)
        if self.is_open():
            self.log("menu already open")
            return self.screen

        self._channels[ELEVATOR] = HIGH
        self._channels[AILERON] = LOW
        self.log("step 1: throttle low, elevator up, aileron left")
        self._pump(hold)
        self._channels[AILERON] = HIGH
        self.log("step 2: aileron right")
        self._pump(hold)
        self._channels[ELEVATOR] = MID
        self._channels[AILERON] = MID
        self._pump(0.5)
        self.settle()
        if not self.is_open():
            raise MenuError("the menu did not open; screen says: %s"
                            % " / ".join(self.screen.rows.get(r, "")
                                         for r in sorted(self.screen.rows)))
        return self.screen

    def is_open(self):
        """A prompting screen mentions entering; an open one has a cursor."""
        text = " ".join(self.screen.rows.values()).upper()
        if "ENTER MENU" in text or "HOLD 5-10" in text:
            return False
        return self.screen.selected() is not None

    # ----------------------------------------------------------------- moving
    def settings(self):
        """Every parameter currently on screen, with its value."""
        self.settle()
        return self.screen.entries()

    def walk(self, limit=40, on_found=None):
        """Step through the whole list, collecting names and values.

        The screen shows a window onto a longer list, so the only way to see
        all of it is to move the cursor to the end. Stops when the cursor stops
        moving, or when it wraps back to where it started.

        `on_found` is called with each entry as it appears. Walking the list
        costs a second per step and the better part of a minute in total, so an
        interface that waits for the return value has nothing to show for most
        of it, which looks exactly like a hang.
        """
        found = []
        seen = set()
        last = None
        for _ in range(limit):
            here = self.screen.selected()
            if here is None:
                break
            if here[0] in seen:
                break                       # back where we started: the list wraps
            seen.add(here[0])
            found.append(here)
            if on_found is not None:
                on_found(here[0], here[1], len(found))
            last = here[0]
            self.next_entry()
            after = self.screen.selected()
            if after is not None and after[0] == last:
                break                       # the cursor refused to move: the end
        return found

    def goto(self, name, limit=40):
        """Put the cursor on a named parameter, from wherever it is."""
        want = name.strip().upper()
        for _ in range(limit):
            here = self.screen.selected()
            if here is None:
                raise MenuError("no cursor on screen")
            if here[0].upper() == want:
                return here
            before = here[0]
            self.next_entry()
            after = self.screen.selected()
            if after is not None and after[0] == before:
                break                       # end of the list, wrap not offered
        raise MenuError("no parameter called %r; the screen offers %s"
                        % (name, ", ".join(n for n, _, _ in self.screen.entries())))

    def bump(self, name, forward=True):
        """Move one parameter one step along its own list of values.

        Stepping rather than jumping to a wanted value is what an interface
        wants: the option lists differ between models and firmware versions, so
        the honest thing to show a person is the value the ESC now reports, not
        the one that was asked for.
        """
        self.goto(name)
        if forward:
            self.next_value()
        else:
            self.previous_value()
        here = self.screen.selected()
        return here[1] if here else None

    def activate(self, name):
        """Trigger an entry that has no value, such as EXIT W/ SAVE.

        The list ends with three of these - save and exit, restore defaults and
        exit, and exit without saving. They are chosen the same way a value is
        changed, with the aileron, because to the ESC they are simply the last
        entries of the same list.

        Nothing is read back afterwards on purpose: these entries end the menu
        session, so the screen that follows is no longer a menu.

        Leaving restarts the ESC, and it announces itself again for well under a
        second (on the bench, from 0.17 to 0.89 s after the entry took) before
        going quiet for good. Whoever is on the wire has to shake hands inside
        that window, or the ESC stays off the bus until its battery is cycled,
        and that includes the flight controller. So the link is picked up again
        here, and counts only once telemetry comes back: a handshake that did
        not take leaves an ESC that control data then silences. Closing the
        session hands the link over, because an Avian that loses its master
        announces itself for as long as nobody answers (over 6 s, measured).
        """
        self.goto(name)
        self.log("activating %s" % name)
        self._channels[AILERON] = HIGH
        self._pump(PULSE)
        self._channels[AILERON] = MID
        self._pump(0.25)

        # Listen only: it announces by itself now, and a probe on the single
        # wire can land on top of an announcement
        self._probing = False
        found = False
        end = time.monotonic() + 3.0
        try:
            while not found and time.monotonic() < end:
                self.device = None
                while self.device is None and time.monotonic() < end:
                    self._pump(0.02)
                if self.device is None:
                    break
                linked = time.monotonic()
                self._pump(0.3)
                found = self._last_telemetry > linked
        finally:
            self._probing = True
        if found:
            # A full second of link before the session ends, as measured: an
            # ESC held that long announced itself for the whole 6 s that
            # followed, which is what the flight controller needs to take over
            self._pump(0.7)
        else:
            self.log("the ESC did not come back after %s: cycle its battery" % name)
        return self.screen

    def set(self, name, value, limit=12):
        """Cycle a parameter's value until it reads what was asked for.

        Cycling rather than counting: the option lists differ between models and
        firmware versions, and the screen is the only authority on what this ESC
        actually offers. If the value never appears, the options that did are
        reported, which is more useful than a timeout.
        """
        self.goto(name)
        want = value.strip().upper()
        offered = []
        for _ in range(limit):
            here = self.screen.selected()
            if here is None:
                raise MenuError("lost the cursor while setting %r" % name)
            current = here[1]
            if current.upper() == want:
                return current
            if current not in offered:
                offered.append(current)
            self.next_value()
            if self.screen.selected() is None:
                raise MenuError("lost the cursor while setting %r" % name)
            if self.screen.selected()[1] == current:
                break                       # the value stopped changing
        raise MenuError("%s never reached %r; it offers %s"
                        % (name, value, ", ".join(offered) or "nothing"))
