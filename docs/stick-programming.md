# Stick programming on an Avian, measured

What a Spektrum Avian announces and expects when programmed from the throttle
alone. Measured on an **Avian 70 A Smart Lite, `SPMXAE70C`**, driven over SRXL2
from a host, with the tones read off a motor phase on an oscilloscope.

Spektrum publish no manual for this model. The two that exist describe different
ESCs — `SPMXAE1015`…`SPMXAE1100` list thirteen parameters, the 130 Pro
(`SPMXAE2130`) lists fifteen — and this one announces sixteen. The parameter
*names and options* come from Spektrum's SmartLink app documentation, which
covers the Avian series as a whole and agrees with what this ESC announces; the
timing is measured here, because nobody documents it.

## Entering

Full throttle present as the ESC powers up. It first runs the throttle
calibration sequence — three ascending tones, then two short ones acknowledging
the high endpoint — and if the throttle has not gone to minimum within five
seconds it enters programming instead of calibrating.

A link is not needed to power up, but it is needed for the ESC to read the
throttle at all: unlinked, it reports 0 % whatever is sent.

**There is no telemetry while in programming.** Zero frames in 35 s with the
link up. Anything that wants to follow the menu has to do it out of band.

## The tone code

The parameter number is announced as long and short tones, and the encoding is
a rule rather than a table:

    parameter = longs × 5 + shorts

Verified against all sixteen. Long tones measure 300 ± 5 ms, short ones
100 ± 5 ms, separated by 200 ± 10 ms inside a group.

## The sixteen parameters

From Spektrum's `SPMXAE1100` programming guide, and confirmed tone by tone:

| # | tones | parameter | default |
|---|---|---|---|
| 1 | 1 short | Aircraft Type | Airplane |
| 2 | 2 short | **Brake Type** | **Disabled** |
| 3 | 3 short | Brake Force | Disabled |
| 4 | 4 short | Voltage Cutoff Type | Surge |
| 5 | 1 long | Number of LiPo Cells | Auto Calc |
| 6 | 1 long 1 short | Cutoff Voltage | 3.4 V |
| 7 | 1 long 2 short | BEC Voltage | 6.0 V |
| 8 | 1 long 3 short | Start-Up Mode | Normal |
| 9 | 1 long 4 short | Timing | Low |
| 10 | 2 long | Motor Rotation | CW |
| 11 | 2 long 1 short | Freewheel Mode | Disabled |
| 12 | 2 long 2 short | Governor Gain | Level 1 |
| 13 | 2 long 3 short | AR Time | 45 |
| 14 | 2 long 4 short | Restart Accel | 1.5 |
| 15 | 3 long | **Thrust reverse** | **Ch7** |
| 16 | 3 long 1 short | *unidentified, two options* | — |

Items 1 to 15 match the SmartLink parameter list exactly, including its own
change log, which numbers "Reverse brake type (item #2)" and "Thrust reverse
channel (item #15)". **Item 16 appears in no document.** Entering it produces a
cycle of two options rather than an immediate action, so it is a parameter with
two values, not a plain "save and exit" — which is what the older manuals
describe as the last item. What its two values do has not been established, and
selecting it blindly is how a session ends up with an ESC in an unknown state.

Two entries decide whether reverse works, and one is not enough: `Thrust
reverse` only chooses the channel, and Spektrum say plainly that "Reverse must
set in the Brake Type menu". Out of the box the channel is already Ch7 and the
function is off.

## The timing

The list scrolls on its own, and the cadence is exact:

    gap between the end of one announcement and the start of the next
        3.19 s, ±5 ms over seven consecutive measurements

    announcement length
        longs × 0.30 + shorts × 0.10 + (tones − 1) × 0.20 seconds

Predicted against measured, items 2 to 10 of one pass:

| item | predicted | measured |
|---|---|---|
| 2 | 13.39 | 13.38 |
| 3 | 16.98 | 16.96 |
| 4 | 20.87 | 20.85 |
| 5 | 25.06 | 25.04 |
| 6 | 28.55 | 28.53 |
| 7 | 32.34 | 32.31 |
| 8 | 36.43 | 36.40 |

The first item starts about 10.1 s after power-up, but that offset moves with
how long the link takes to come up, so it is not safe to schedule from it.
**Identify the item from its tones and extrapolate at most two or three steps.**
Extrapolating a dozen items ahead does not work: the error accumulates and the
action lands on the wrong parameter, which is how several attempts here ended up
selecting something other than what they intended.

## Inside a parameter

**Throttle to minimum** within three seconds of an announcement enters that
parameter. The ESC then announces its options on the same cadence:

    first option        4.05 s after the throttle goes down
    each one after      3.19 s after the end of the previous one
    after the last      a doubled gap, 6.6 s, then it starts again

Measured on Brake Type, whose four options run 1, 2, 3, 4 short tones. The ESC
cycles them **indefinitely** — three full cycles over 43 s with no sign of giving
up — so there is no need to rush a selection.

**The cycle always starts at option 1, not at the current value.** Entering the
same parameter before and after a change produces exactly the same sequence, so
*the current setting cannot be read back from the menu*. Anything that needs to
know the present value has to get it some other way.

## Choosing an option

**Throttle back to full** while an option is being announced chooses it, and the
ESC answers: a **long tone, 0.40 s, appended to that option's own tones**. An
option announced normally is only its short tones — option 4 is `b b b b`,
1.00 s — so a group that reads `b b b b L0.40` is the acknowledgement, and it is
the one unambiguous confirmation the ESC gives.

After the acknowledgement the ESC **returns to the top of the parameter list**
and announces item 1 about 3.8 s later. The 130 manual says a selection makes it
"continue down the list"; that is not what this ESC does, so resuming at item 1
is not evidence that the selection failed.

## Reading the tones without an oscilloscope

A beep is the ESC energising the windings, so it appears on a motor phase as a
burst of switching. A rolling standard deviation over 5 ms windows separates
burst from silence with about 15:1 margin.

The sample rate is the thing to watch: detection is reliable at 25 kSa/s and
above and falls apart around 14 kSa/s, which sets a ceiling on how long a single
capture can be. Short captures taken close to each decision beat one long one.

For a tool that has to run without a scope, the cadence above is deterministic
enough to work open loop — but only when each action is anchored to a freshly
identified item, not counted from power-up.

## Reading the tones with an oscilloscope

Deep memory is the only usable source, and **deep memory can only be read with
the instrument stopped** — a running scope returns zero points. A continuous
live feed is therefore not available: screen-memory reads work while running but
carry 120 points over a 5 s window in roll mode, 42 ms per point, which cannot
separate a 100 ms tone from a 300 ms one.

What works is a single deep capture spanning the whole experiment: 110 s at
10 Mpoints is 90 kSa/s, with no blind interval anywhere in it. Actions are then
timed open loop from the cadence above and *verified afterwards from the same
capture*, which is more reliable than reacting live and leaves a record.

Two practical points. The capture must not starve the link: the ESC drops it
after about 250 ms without a frame, and reading the scope takes longer than
that, so the link belongs on its own thread. And the trigger must be a channel
that actually carries signal — a scope waiting on a silent channel stays in WAIT
forever and returns nothing.

## Still unknown

* **How a change is committed**, and whether it survives a power cycle. The
  acknowledgement tone shows the ESC registered the choice; nothing yet shows it
  was stored.
* **What item 16 is** and what its two options do.
* After a session that entered item 16 without choosing either option, this ESC
  reports **0 % throttle at every pulse width**, across power cycles and after a
  fresh throttle calibration, with the link up and telemetry flowing normally
  (24.9 V, correct temperatures). Whether that is a consequence of the
  programming session or of running with the motor disconnected is not yet
  separated — both changed at the same time.
