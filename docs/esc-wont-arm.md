# An ESC that links, reports, and will not take throttle

A record of a failure that is not understood yet, written down so the next
session starts from what was eliminated rather than repeating it.

## The symptom

The ESC links: it announces itself, answers the handshake, and streams
telemetry with sensible values - 24.73 V, correct temperatures, a stable frame
rate. It also beeps continuously the way an ESC does when it sees no receiver.

And it reports **0.0 % throttle at every commanded position**, from 1000 to
2000 microseconds, with zero rpm and zero current. It has been like this since
an afternoon of stick programming; before that, the same code made the same
motor turn.

## What has been eliminated

Each of these was tested, not reasoned about:

- **The motor being disconnected.** It is connected now, and nothing changed.
- **Brake Type = Reverse**, which had been saved earlier. A factory reset put
  it back to Disabled - confirmed by reading the menu afterwards - and the
  throttle is still refused.
- **The neutral point moving.** Booting at 1500 and sweeping both ways gives
  0 % in both directions.
- **The reverse channel.** CH7 low, centre and high, each against the whole
  throttle range: nothing.
- **Throttle calibration.** Three attempts, including one using the exact
  timings from INAV's own driver (3 s at full after power-up, 5 s at minimum).
  The ESC answers the sequence - it changes its tone - and the result is the
  same.
- **The frame's channel layout.** All channels low, all centred, and the
  arrangement a transmitter actually sends (sticks centred, aux at rest).
- **The channel scaling.** Python and the firmware produce identical values:
  1000 us to 768, 1500 us to 0x8000, 2000 us to 64768.
- **The handshake.** Byte for byte the same frame the firmware builds, and the
  bytes were verified as they left the pad by asking the adapter to report its
  own echo.
- **The baud in the reply.** The ESC advertises support for 115200 only;
  answering with 115200 rather than 400000 changes nothing.
- **Talking over the announcement.** Replying only in the gaps, and replying
  repeatedly, changes nothing.
- **The adapter's firmware**, which was modified several times that evening. The
  sequence it now watches for cannot occur inside a channel frame - checked
  across every throttle value and both channel layouts.

## What was learned on the way

**An ESC that keeps announcing is not refusing.** A running Avian continues to
send handshakes and the master answers them; INAV's driver does exactly that on
purpose. An hour was spent reading repeated announcements as a rejection.

**All channels at 1000 microseconds is a command, not a neutral.** Throttle low,
aileron left and elevator down is the combination that opens the configuration
menu, and the ESC was seen stepping through the entry prompts - on its own text
page - while it appeared to be ignoring the throttle. The text page is the only
channel that said anything true all evening, and it had not been read in that
condition until late.

**The ESC will link without a power cycle.** It chimed when a tool started
talking to it while it was already powered.

## What to try next

1. **Read all fifteen parameters.** The walk was interrupted. A factory reset
   should leave every one at its default; one that is not would explain
   everything. `LIPO CELLS`, `CUTOFF VOLT` and `FLIGHT MODE` are the ones that
   could refuse a throttle while leaving telemetry intact.
2. **Try the other ESC.** An `SPMXAE70B` is on hand. It is the only test that
   separates a damaged controller from a wrong host, and everything above has
   failed to do that.
3. **The unidentified menu entry.** Earlier that day, while looking for an exit
   in the tone menu, item 16 was selected twice without knowing what it was.
   It is the one action taken blind, it appears in no manual, and a factory
   reset demonstrably does not undo whatever it did - the reset works, since it
   restored Brake Type.
