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
