# The application

A window for reading and changing an Avian's settings, through a flight
controller running INAV or through the SRXL2 adapter. One file, nothing to
install.

## What it needs

**Through an aircraft.** A flight controller running INAV with a serial port
assigned to Spektrum Smart ESC, the ESC on that port, and the board's USB cable.
Nothing else: the tool speaks MSP to open the port as a raw pipe and then talks
to the ESC directly through it.

**On a bench.** The ESP32 adapter in `esp32-bridge/`, which drives the single
wire from a dev board.

The tool finds the board itself. Vendor and product ids only decide what to try
first; every port is then asked a question only the right device can answer -
`MSP_FC_VARIANT` for a flight controller, its own identify command for the
adapter - so a port is never claimed on the strength of its driver's label.

## Using it

1. Plug in the board and start the application. It scans every serial port at
   once and selects what it found.
2. Press **Connect**, then **switch the ESC on**. The order matters: an Avian
   announces itself for a few seconds after power-up and then goes quiet, so
   the tool has to be listening first. The window says when it is waiting.
3. It opens the ESC's menu - about twenty seconds, which is the ESC's own
   requirement, not slowness - and then reads every parameter.
4. Change a value with the arrows either side of it. Each press is sent to the
   ESC and the value shown is the one it reports back afterwards.
5. **Save to ESC** keeps the changes. **Restore defaults** puts the ESC back to
   how it left the factory. **Discard** leaves without writing.

## The one thing to remember

A change takes effect immediately and is **gone at the next power-up** unless
Save to ESC is pressed. That is the ESC's design, not the tool's: its menu has
separate entries for leaving with and without writing. An afternoon was lost to
this before it was understood - the ESC accepts a change, confirms it, and then
forgets it, which is indistinguishable from it having ignored the change.

## Building it

    python packaging/make_icon.py
    python -m PyInstaller --noconfirm --onefile --windowed \
        --name SmartEscTool --icon packaging/icon.ico \
        --add-data "packaging/icon.ico;." --add-data "packaging/icon.png;." \
        packaging/app.py

Pillow is needed for the first line only; the application itself uses the
written icon and never imports an imaging library.

## Trying it without an aircraft

INAV's SITL runs the same firmware as a board and exposes each of its UARTs as
a TCP socket, so the whole path can be exercised with nothing on the bench but
the ESC:

    tool -> TCP 5760, SITL's MSP
         -> passthrough
         -> SITL's UART2, exposed as TCP 5761
         -> a relay
         -> the SRXL2 adapter
         -> the wire
         -> the ESC

The tool finds SITL by itself and prefers it, because a flight controller is a
better answer than an adapter when both are present.

Two settings are needed in SITL's CLI, and the second is easy to miss:

    serial 1 536870912 115200 115200 0 115200
    set motor_pwm_protocol = SRXL2
    save

Assigning the function is not enough. `MSP_SET_PASSTHROUGH` looks for the port
by function and then for a port *usage* - a port that is actually open - and
nothing opens it until the motor protocol makes the SRXL2 output run. Without
the second line the passthrough is refused with a plain zero and no
explanation.

One behaviour worth knowing: `serialPassthrough()` blocks in a loop and the
scheduler stops with it, so a flight controller in passthrough answers nothing
else until the session ends with `+++`. A tool that opens passthrough and then
walks away leaves the board deaf until it is restarted.

### What this proves, and what it does not

Proven: the MSP handshake, choosing the port by function id, the passthrough
relaying bytes in both directions, and this tool's INAV transport - against the
real firmware, with a real ESC answering at the far end.

Not proven: that a physical board behaves the same. SITL's UART is a socket,
not an STM32 pad in single-wire half duplex, and USB timing on a real board is
its own question.

## Trying the INAV path without a flight controller

INAV has no ESP32 port - 254 targets, every one of them STM32 or AT32 - so the
adapter cannot run the firmware. It can answer as it, which is the half that
matters for testing a host: send it MSP and it replies as INAV would, and after
`MSP_SET_PASSTHROUGH` it becomes a transparent pipe to the wire, closing on
`+++`. The tool then takes the same path it will take with an aircraft, over a
real USB serial port, with a real ESC answering.

    smart-esc-tool COM5 config --via inav

Nothing has to be set for this. The adapter's own protocol is SLIP framed and
MSP opens with `$M<`, so the two cannot be confused and neither end needs to
remember a mode.

Three differences from a board are worth knowing, because each one was a bug
before it was a note.

**The rate.** A flight controller mirrors the host's line coding onto the port
it is bridging, so setting the rate sets the ESC's wire. The adapter's USB rate
*is* the link, and lowering it to 115200 replaces every byte with noise. The
transport decides which it is talking to from the rate that answered MSP, and
refuses to touch a rate it does not own.

**The echo.** A single wire returns the host's own bytes. The adapter passes
them back in passthrough rather than swallowing them, because a host that is
never shown an echo is not ready for a board that sends one.

**The reset.** Opening a USB serial port asserts DTR and RTS, which restarts
every ESP32 development board. Nothing answers for a second or two afterwards,
so a single silent attempt proves nothing.
