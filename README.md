# smart-esc-tool

An open tool for reading, configuring and eventually updating Spektrum Smart
ESCs, the Avian family, without the manufacturer's USB dongle.

These controllers speak SRXL2 on their throttle wire: a documented protocol on an
ordinary serial port. Everything the proprietary programmer does over that wire can
be done over any serial port, including the one already inside an aeroplane.

## Status

Configuration works, and has been exercised against a real ESC on a bench with a
programmable supply and an oscilloscope. What has been done, on hardware:

* the handshake, and the link that follows it
* telemetry decoded and read back: rpm, current, voltage, throttle
* the stick-programming menu walked, settings read, changed and saved
* the throttle driven across its whole range, with the supply's current as the
  witness rather than the ESC's own word
* thrust reverse engaged and released, at rest and while the motor was turning
* endpoint calibration, checked by moving the ESC's full-throttle point to
  1800 us and back to 2000 us and measuring the scale each time

What has **not** been done: updating firmware, which is described below and is
still a goal rather than a feature. The flight-controller route has also not been
run against a real board yet; the bench work all went through the ESP32 adapter.

## Why

An Avian's settings, thrust reverse, brake mode, timing, cutoff, BEC voltage, can
be changed from a Spektrum transmitter, or from a programming box you buy, or from
a USB dongle you buy. If you own a model that came with one of these ESCs and no
Spektrum radio, the only route to its own settings is through hardware sold for
the purpose.

The wire does not require any of that.

## Two ways in

Both present the same interface, so everything above them is written once.

**Through an ESP32.** This is the route the bench work used. `esp32-bridge/` holds
a sketch that turns a dev board into the electrical half of a flight controller's
SRXL2 port: one pad carrying both directions through the GPIO matrix, released
except while transmitting, with microsecond timestamps on everything received.
That last part is why it exists, because a USB serial adapter can tell you what
arrived but not when.

**Through a flight controller.** A board running INAV with a port assigned to
Spektrum Smart ESC can hand that port to a PC: `MSP_SET_PASSTHROUGH` with the
serial function id bridges the USB connection straight to the ESC's wire. The
flight controller's own scheduler stops while it does this, so nothing transmits
over the session, the port stays in single-wire half duplex, and the host's baud
rate is mirrored onto the wire every 15 ms, which means the SRXL2 handshake can
negotiate 115200 up to 400000 straight through it. The session ends with `+++`.

Nothing needs adding to INAV for this; the passthrough has been there for years.

## What the ESC turned out to be like

Four things the specification does not tell you, all measured, all of which shape
how the tool has to behave:

* **It introduces itself once.** An Avian announces itself six times in the 300 ms
  after it gains power and is silent from then on. It never speaks unprompted. So
  the tool has to be running and talking before the ESC is powered, and a board
  that reboots underneath a powered ESC will never link to it.
* **The link dies in about a quarter of a second** without a frame, and comes back
  as soon as frames resume, with the throttle picking up where it left off. This
  is why the transport is fed by a thread that does nothing else.
* **Asking for telemetry too often stops it taking throttle.** Requesting on every
  frame leaves the ESC holding the link and refusing to run. Asking every second
  frame or less often is fine. The ESC answers about two requests in three and
  rotates between three sensor types, so its own data arrives at roughly the
  request rate divided by nine.
* **The throttle percentage it reports is the command measured against its own
  learned endpoints**, not an echo. Calibrated to 1800 us it reports 25, 50, 75,
  100 and 100 per cent for 1200 to 2000; calibrated to 2000 it reports 20, 40, 60,
  80, 100. That makes it the cheapest way to read back where calibration left the
  endpoint.

Also worth knowing before the first run: the startup tones last about five seconds
after the announcement, and the motor will not turn until the last of them has
sounded.

## Running it

A window:

    python -m smart_esc_tool.gui

The interface is PySide6 with Fluent components. The ESC will not hold a link for
longer than a quarter of a second, so a worker thread owns the transport and the
menu while a person reads the screen, and every value shown is one the ESC
reported after the change rather than the value that was asked for.

Or a command line, where `<port>` is the serial port and the route defaults to the
ESP32 adapter:

    python -m smart_esc_tool COM5 find              # who is on the wire
    python -m smart_esc_tool COM5 link              # handshake and hold the link
    python -m smart_esc_tool COM5 telem             # decode what it reports
    python -m smart_esc_tool COM5 menu              # walk the settings menu
    python -m smart_esc_tool COM5 set  "Thrust Rev.=REVERSE"
    python -m smart_esc_tool COM5 save "Thrust Rev.=REVERSE"
    python -m smart_esc_tool COM5 reset             # back to factory settings
    python -m smart_esc_tool COM5 selftest          # checks the ESP32 adapter
    python -m smart_esc_tool COM5 find --via inav   # through a flight controller

`set` changes a setting for the session; `save` writes it and leaves through the
menu's save entry.

Releases carry a Windows `.exe` with nothing to install.

## Updating firmware

This is the part the dongle is really sold for, and the design is deliberate:
**this project will never contain a firmware image.**

The manufacturer publishes them. HobbyWing, whose design the Avian is and whose
software the Spektrum USB Link is a rebadge of, serves a manifest at
`/Upload/software/app_version_control.xml` naming the current database, and that
database is a plain SQLite file whose `software` table holds one image per
hardware revision and version, keyed to the `controller` table. It is a public
URL, no credentials of any kind.

So the tool would fetch from the vendor, on demand, the same file their own
application fetches, and push the image the connected ESC asks for. Nothing is
redistributed and nothing is decrypted: the images are opaque and stay opaque,
because delivering one does not require understanding it.

**What is missing is the last step.** How an image is transferred to the ESC is
not published, and cannot be inferred from the database. It is the one piece that
needs observing rather than reasoning about.

> **If you own an SPMXCA200 programmer or a Spektrum USB Link, a capture of its USB
> traffic during an update would unblock this entirely.** That is the single most
> useful contribution anyone could make to this project.

Until then, firmware updating is a stated goal and not a feature. A failed write
bricks an ESC, and there is no recovery path we know of, so the order of work is:
read parameters, then write parameters, which a factory reset undoes, then, only
against a protocol that is understood rather than guessed, firmware.

## Safety

* **No propeller.** Not for any step, including the ones that look harmless.
* **The throttle connector's middle pin is a BEC output**, 5 V to 8.4 V depending
  on the model and up to 12 V on a 130 Pro. It powers a receiver; it does not
  accept power, and it will destroy a 3.3 V input. When wiring an ESP32, use the
  signal and the ground and leave the middle one alone.
* The ESC runs from its own battery. Ground is common, the supplies are not.

## Licence

GPL-3.0. The SRXL2 implementation is an independent reading of Spektrum's published
specification; no manufacturer code or data is included or distributed.

Spektrum, Avian and HobbyWing are trademarks of their respective owners. This
project is not affiliated with, endorsed by, or supported by any of them.
