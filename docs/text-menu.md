# The Avian's own menu, over the wire

An Avian carries a configuration menu that it draws as text and navigates with
stick positions. Spektrum expose it to their transmitters — "scroll to the last
telemetry page… use the aileron and elevator to navigate the menu and make
selections" — but nothing in it is transmitter-specific. It rides on ordinary
SRXL2: channel values in, telemetry out. Any host that can put frames on that
wire can drive it, which through a flight controller's serial passthrough means
a PC on the other end of the aircraft's USB cable.

This is the interface worth building a tool on. The alternative — programming
from throttle tones alone — is write-only: it acknowledges a change with a beep
and can never tell you what the ESC is currently set to.

Measured on an `SPMXAE70C`. Spektrum publish no manual for the Smart Lite
series, so everything here comes from the ESC.

## The screen

Telemetry frames with sensor id **`0x0C`**, one screen row each:

    dest, 0x0C, sID, row, thirteen characters

Reassembled, they are a title and then alternating name and value rows, with a
`>` in the first column marking the cursor:

     Avian Prog
    >BRAKE TYPE
         Disabled
     BRAKE FORCE
                0

One row per frame means a screen takes a moment to arrive and is briefly
inconsistent while it does. Wait for it to stop changing — about 0.8 s of quiet
is enough — rather than acting on the first frame mentioning the row you want.

The window is five entries. **The list is longer than the window**, so a single
read is not the whole menu; the cursor has to be walked to the end to see it.

## Getting in

Throttle stays at minimum throughout. The screen narrates the sequence itself,
which is better than following a script — an ESC already in the menu must not be
sent through it again:

| step | screen says | channels |
|---|---|---|
| 1 | Low Throt, Up Elev, Left Aile, *Hold 5-10sec* | elevator high, aileron low |
| 2 | Low Throt, Up Elev, Right Aile | elevator high, aileron high |

The ESC announces itself unprompted only in a short window after power-up. A
host that arrives later — the normal case, aircraft already powered — has to
call it, and the address is not known in advance, so sweep `0x40`…`0x4F`.

## Moving around

| action | effect |
|---|---|
| elevator up | cursor to the next entry |
| elevator down | cursor to the previous entry |
| aileron right | next value |
| aileron left | previous value |

Each is a **pulse**: the channel to one extreme for about a third of a second,
then back to centre — what a hand on a stick does. Holding an extreme is only
for entering. The value on screen changes immediately, so every action can be
verified by reading it back instead of being assumed.

## The whole list

Fifteen parameters and then three actions:

     1 FLIGHT MODE      9 MOTOR ROTATE
     2 BRAKE TYPE      10 ACTIVE FW
     3 BRAKE FORCE     11 GOV GAIN
     4 CUTOFF TYPE     12 AR TIME
     5 LIPO CELLS      13 RESTARTACCEL
     6 CUTOFF VOLT     14 THRUST REV
     7 BEC VOLTAGE
     8 STARTUP TIME    15 EXIT W/ SAVE
                       16 DEFAULT/EXIT
                       17 EXIT

Past the last one the cursor wraps back to `FLIGHT MODE`.

The three actions have no value and are triggered like one — aileron right.

**`EXIT W/ SAVE` is not optional.** A changed value appears on screen at once
and works immediately, but it is gone at the next power-up unless this entry is
used. A session that ends any other way has changed nothing permanently, which
is easy to mistake for the ESC ignoring the change.

`DEFAULT/EXIT` restores the factory settings. `EXIT` leaves without writing.

## Reverse, specifically

Two entries are involved and one alone does nothing. `THRUST REV` only picks the
channel that commands it, defaulting to `CH7`; the function itself lives in
`BRAKE TYPE`, whose fourth value is `Reverse`. Spektrum say it plainly — "Reverse
must set in the Brake Type menu" — and add that the chosen channel must be an
otherwise unused one, and that `BRAKE FORCE` is best at 7 when running reverse.

Verified end to end on the bench: defaults restored, `BRAKE TYPE` set to
`Reverse`, saved, and still reading `Reverse` after the supply was cycled.
