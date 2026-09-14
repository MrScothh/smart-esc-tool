# smart-esc-tool

An open tool for reading, configuring and eventually updating Spektrum Smart
ESCs — the Avian family — without the manufacturer's USB dongle.

These controllers speak SRXL2 on their throttle wire: a documented protocol on an
ordinary serial port. Everything the proprietary programmer does over that wire can
be done over any serial port, including the one already inside an aeroplane.

> **Status: nothing here has talked to a real ESC yet.** The protocol is written
> from Spektrum's published specification and checked against their own library;
> the transports are written and unit-tested; no byte has crossed a physical wire.
> Treat every claim below as a design, not a result, until this notice changes.

## Why

An Avian's settings — thrust reverse, brake mode, timing, cutoff, BEC voltage —
can be changed from a Spektrum transmitter, or from a programming box you buy, or
from a USB dongle you buy. If you own a model that came with one of these ESCs and
no Spektrum radio, the only route to its own settings is through hardware sold for
the purpose.

The wire does not require any of that.

## Two ways in

Both present the same interface, so everything above them is written once.

**Through a flight controller.** A board running INAV with a port assigned to
Spektrum Smart ESC can hand that port to a PC:
`MSP_SET_PASSTHROUGH` with the serial function id bridges the USB connection
straight to the ESC's wire. The flight controller's own scheduler stops while it
does this, so nothing transmits over the session, the port stays in single-wire
half duplex, and the host's baud rate is mirrored onto the wire every 15 ms — which
means the SRXL2 handshake can negotiate 115200 up to 400000 straight through it.
The session ends with `+++`.

Nothing needs adding to INAV for this; the passthrough has been there for years.

**Through an ESP32.** For a controller on a bench with no aircraft attached.
`esp32-bridge/` holds a sketch that turns a dev board into the electrical half of a
flight controller's SRXL2 port: one pad carrying both directions through the GPIO
matrix, released except while transmitting, with microsecond timestamps on
everything received. That last part is why it exists — a USB serial adapter can
tell you what arrived, but not when.

## What it will do

**Read and write parameters.** The Avian exposes its settings over the same
telemetry that carries voltage and current; Spektrum's own transmitters draw a menu
from it, which is what their "Avian Prog telemetry screen" is. A PC can draw the
same menu.

**Update firmware.** This is the part the dongle is really sold for, and the design
is deliberate: **this project will never contain a firmware image.**

The manufacturer publishes them. HobbyWing — whose design the Avian is, and whose
software the Spektrum USB Link is a rebadge of — serves a manifest at
`/Upload/software/app_version_control.xml` naming the current database, and that
database is a plain SQLite file whose `software` table holds one image per hardware
revision and version, keyed to the `controller` table. It is a public URL, no
credentials of any kind.

So the tool fetches from the vendor, on demand, the same file their own application
fetches, and pushes the image the connected ESC asks for. Nothing is redistributed
and nothing is decrypted — the images are opaque and stay opaque, because
delivering one does not require understanding it.

**What is missing is the last step.** How an image is transferred to the ESC is not
published, and cannot be inferred from the database. It is the one piece that needs
observing rather than reasoning about.

> **If you own an SPMXCA200 programmer or a Spektrum USB Link, a capture of its USB
> traffic during an update would unblock this entirely.** That is the single most
> useful contribution anyone could make to this project.

Until then, firmware updating is a stated goal and not a feature. A failed write
bricks an ESC, and there is no recovery path we know of, so the order of work is:
read parameters, then write parameters — which a factory reset undoes — then, only
against a protocol that is understood rather than guessed, firmware.

## Running it

    python -m smart_esc_tool COM5 listen              # via the ESP32 adapter
    python -m smart_esc_tool COM5 find --via inav     # via a flight controller

Releases carry a Windows `.exe` with nothing to install.

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
