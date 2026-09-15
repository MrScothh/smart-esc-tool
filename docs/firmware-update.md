# Updating an Avian's firmware: what stands in the way

An investigation, not an implementation. Nothing here was tried on an ESC, and
the conclusion is that the missing piece is not code.

## The images are not the problem

They are published and versioned, and there are two ways to them.

Spektrum's own Windows application ships a SQLite database - `AIRCRAFT_ESC_settings.db3` -
where every `software` row carries a `bootloader` BLOB and a `bootloader_version`,
keyed to a hardware string like `HW195_V1.00456NB` or `HW198_V1.00456NB`. Fifteen
images in the copy examined here.

HobbyWing, who build the hardware, publish a live manifest at
`hobbywing.com/Upload/software/app_version_control.xml`, which names the current
database - version `2026-08-17` when this was written - and where to fetch it.
Their Android application carries a 15 MB copy with 97 images.

So a tool can fetch what it needs from the vendor rather than redistributing it.

**The images are encrypted.** Entropy is 7.998 to 7.999 bits per byte, there is
no padding and no readable string in any of them. Every length is a multiple of
eight, and two images of the same firmware version for different hardware begin
with the same eight bytes - so: a 64-bit block cipher in ECB, with a header that
is constant for a given version. This matters less than it looks, because there
is no reason to decrypt anything: the ESC's bootloader is what decrypts, and a
tool that ships the blob through unchanged never needs the key.

## The path is the problem

Neither official application updates over the throttle wire.

**Windows.** `Spektrum USB Link.exe` is a Delphi application and it ships
`hidapi.dll`: it talks **USB HID** to the SPMXCA200 programming box, which in
turn drives the ESC's three-pin programming port - the separate connector, not
the servo lead that carries SRXL2.

**Android.** HobbyWing's `HW Link V2` talks **BLE** - vendor services `F1F0`
and `F2F0`, each with a transmit and a receive characteristic - or WiFi, to
their own box. Its `Core` class slices a firmware image into numbered packets
with `setFirmware`, keeps them in a sparse array, and pushes them one at a time
through `sendFirmware` with a per-packet timeout that differs between BLE and
WiFi. The block size constant is 1024.

That last one is background rather than a target: HobbyWing's box is widely
reported not to work with Spektrum-branded ESCs, which use a different protocol
even though the hardware underneath is the same.

**Nothing observed so far suggests a firmware path exists over SRXL2 at all.**
The ESC ignores `ParamConfig` (0x50) on that wire, and its configuration is
exposed as a text page over telemetry instead - which is what this tool drives.
A bootloader entry on the throttle wire would be a surprise, not an expectation.

## The ESC introduces itself

Listening on the programming port, with nothing attached to it and nothing
transmitted, settles part of this. About three seconds after power-up the ESC
sends, unprompted:

    31 34 32 32 39 32 32 31        "14229221"

Eight ASCII digits, once, and then silence. Identical on every power-up -
six cycles in one session and four in another, byte for byte.

The rate was read from the line rather than guessed. Pulse widths cluster at
**52, 104 and 156 microseconds** - one, two and three bit times - which puts
the bit at 52 us and the line at **19200 baud, 8N1**, 2% from the standard
rate and nowhere near any other. The first attempt took the shortest pulse in
a window as the bit time and got 51 us from a single sample; the clusters say
the same thing with every pulse voting, and that is the number to trust.

There is no framing around it: no opening byte, no length, no checksum. The
port speaks text.

So the earlier conclusion here - that without an SPMXCA200 there is nothing to
reverse - was wrong, and pleasantly so. **The ESC's half is readable without
owning anything.** What is still missing is the reply: what the box says back
to that greeting, and whether the ESC then expects a command, an
acknowledgement, or a version number.

## What would actually be needed

The other half of the conversation is still only inside the box. The Windows
application knows how to talk to the *box*; the box knows how to talk to the
ESC, and only the greeting above has been seen from the outside.

Two routes, in order of cost:

1. **Watch the box.** With an SPMXCA200 and the official application, capture
   the programming port while a real update runs. The adapter is already on
   that wire and already reads it. This settles what answers the greeting, the
   framing, the block size, the acknowledgements and the recovery behaviour in
   one session.
2. **Speak HID to the box.** Extract the report format from the Delphi binary
   and drive the box directly. Useful only if the box is a transparent bridge,
   which is exactly what route 1 would establish.

## Why this is not the next thing to build

Changing a menu setting is reversible: the worst case is a wrong value and
another visit to the menu. Writing a bootloader is the one operation that can
leave an ESC that no longer answers, and no recovery path is documented
anywhere - not in the manuals, not in the applications, not in the databases.
Guessing at a flashing protocol against a live ESC is how a controller becomes
scrap, and the ESC is the part that cannot be re-flashed once it stops
listening.

## Who else has been here

`frankiearzu/DSMTools` programs Avian parameters over TextGen - the same page
this tool drives - but from an EdgeTX radio, needing a Spektrum Smart receiver,
and it documents no protocol. It does not attempt firmware updates.

No open work was found that reaches the ESC's configuration from a PC through a
flight controller, or that updates an Avian's firmware outside the vendor's own
applications.
