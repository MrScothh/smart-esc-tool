# An ESC that links, reports, and will not take throttle

This was an unexplained failure for a day and a half. It is explained now, and
the explanation is short, so the long account of what was eliminated has been
replaced by what it turned out to be.

## The symptom

The ESC links: it announces itself, answers the handshake, and streams telemetry
with sensible values - correct voltage, correct temperatures, a steady frame
rate. It also beeps the way an ESC does when it sees no receiver.

And it reports **0.0 % throttle at every commanded position**, from 1000 to 2000
microseconds, with zero rpm and zero current.

## The cause

**Telemetry was being requested on every control frame.** At 50 Hz the ESC keeps
the link, keeps answering - more often than ever, 128 telemetry frames in four
seconds - and quietly stops obeying the throttle.

Ask less often and it obeys immediately, with no power cycle. Measured on an
Avian 70 A against a fixed 1250 us command, in a single power-up:

| requests | throttle reported | rpm | telemetry in 4 s |
|---|---|---|---|
| every 4th frame, 12.5 Hz | 14.0 % | 6606 | 32 |
| every 3rd frame, 16.7 Hz | 14.0 % | 6605 | 43 |
| every 2nd frame, 25 Hz | 14.0 % | 6610 | 64 |
| **every frame, 50 Hz** | **0.0 %** | **0** | **128** |

The 50 Hz row was hit twice in that run, with recovery both times, so it is the
rate and not an accident of ordering.

## What it was not

**Not the number of channels in the frame**, which is what was believed for a
day. With the request rate held fixed at 16.7 Hz, masks of one channel, two
adjacent, two spread apart, four and eight all gave the same 14.0 % and the same
~6610 rpm. With the rate at every frame, all of them gave 0.0 %, the
single-channel mask included - the very mask that had appeared to be the cure.
The original evidence had varied both things at once.

**Not the configuration menu.** Entering it needs throttle low with aileron and
elevator at their extremes, which all-channels-at-1000 happens to satisfy; that
explains some beeping, but the failure happens with the sticks centred too.

**Not brake type, calibration, the neutral point, the reverse channel, the
handshake, the baud, or the channel scaling.** Each was tested and cleared
before the rate was found.

## What follows from it

- The INAV driver never asks on every frame: `SRXL2_TELEM_REQUEST_MIN` is two,
  clamped in code as well as in the settings table, so no stored configuration
  can select it.
- `esc_srxl2_telemetry_rate` no longer offers 50 Hz at all.
- This tool does ask on every frame, deliberately - it wants the ESC's text page
  as fast as it can get it, and the motor is meant to be still while a menu is
  open. The ESC refusing throttle while the tool is connected is a consequence
  worth knowing about, not a fault to chase again.

## Reverse, once the rate was right

With the request rate at every third frame, a frame carrying the throttle and
channel 7 together drives reverse exactly as documented: channel 7 low then high,
against an unchanged 1250 us throttle, gave counter-clockwise, clockwise,
counter-clockwise, clockwise - each at the same 14.0 % and about 6610 rpm. The
throttle goes on meaning throttle, and the telemetry is silent about direction, so
the motor itself is the only instrument for this one.
