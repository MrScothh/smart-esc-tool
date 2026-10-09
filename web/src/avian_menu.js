// Driving an Avian's on-screen menu from a host, over either transport: ported from smart_esc_tool/avian_menu.py,
// where the measurements behind every number below are described.
//     entering        throttle low, elevator up, aileron left held ~8 s, then aileron right held ~8 s
//     elevator up     cursor to the next parameter       elevator down   to the previous one
//     aileron right   next value                         aileron left    previous value
// A command is a pulse, a channel to one extreme for about a third of a second and back to centre, and every action
// is verified by reading the screen back.
import * as srxl2 from "./srxl2.js";
import { Screen, TEXTGEN_SENSOR } from "./textgen.js";
import { sleep } from "./serial_link.js";

export const THROTTLE = 0;
export const AILERON = 1;
export const ELEVATOR = 2;

export const LOW = srxl2.usToValue(1000);
export const MID = srxl2.usToValue(1500);
export const HIGH = srxl2.usToValue(2000);

const PULSE = 350; // ms a stick is held at an extreme
const RECENTRE = 250; // and rests in the middle afterwards
const SETTLE = 800; // no row has changed for this long: the screen is stable
const STEP_HOLD = 8000; // the screen asks for five to ten seconds

export class MenuError extends Error {}
export class LinkLost extends MenuError {}

const now = () => performance.now();
const hex = (b) => `0x${b.toString(16).toUpperCase().padStart(2, "0")}`;

export class AvianMenu {
  constructor(transport, { device = null, log = () => {}, clock = now, wait = sleep } = {}) {
    this.br = transport;
    this.device = device;
    this.screen = new Screen();
    this.log = log;
    this.clock = clock;
    this.wait = wait;
    this.sink = new Uint8Array(0);
    this.lastChange = 0;
    this.channels = { 0: LOW, 1: MID, 2: MID, 3: MID, 4: MID, 5: MID, 6: MID, 7: MID };
    this.probe = srxl2.ESC_ID_FIRST;
    this.probing = true;
    this.lastTelemetry = 0;
  }

  // Keep the link alive and absorb whatever comes back. The ESC drops the link after about a quarter of a second
  // without a frame, so nothing here may block for longer than that.
  async pump(ms) {
    const end = this.clock() + ms;
    let next = 0;
    let nextHandshake = 0;
    for (;;) {
      const t = this.clock();
      if (t >= end) {
        return;
      }
      if (this.device !== null && t >= next) {
        next = t + 20;
        // telemetry asked on every frame: the text page is all the ESC answers and it is wanted fast; at this rate
        // the ESC keeps the link but stops obeying the throttle, which suits a menu (see docs/using-the-app.md).
        // Only after the ESC has shaken hands: control data from an unknown master silences an Avian.
        await this.br.write(srxl2.controlData(this.channels, this.device));
      }
      if (this.device === null && this.probing && t >= nextHandshake) {
        // an Avian with no master announces itself; calling covers the case where it stays quiet anyway
        nextHandshake = t + 100;
        await this.br.write(srxl2.handshake(this.probe, srxl2.BAUD_BIT_400K));
        this.probe = this.probe >= srxl2.ESC_ID_LAST ? srxl2.ESC_ID_FIRST : this.probe + 1;
      }
      for (const event of this.br.poll()) {
        if (event.tag === "R" && event.data.length) {
          const joined = new Uint8Array(this.sink.length + event.data.length);
          joined.set(this.sink);
          joined.set(event.data, this.sink.length);
          this.sink = joined;
        }
      }
      if (this.sink.length >= 5) {
        const { frames, rest } = srxl2.split(this.sink);
        this.sink = rest;
        for (const frame of frames) {
          await this.absorb(frame);
        }
      }
      await this.wait(2);
    }
  }

  async absorb(frame) {
    const body = frame.subarray(3, frame.length - 2);
    if (frame[1] === srxl2.HANDSHAKE && this.device === null) {
      if (frame[3] >= srxl2.ESC_ID_FIRST && frame[3] <= srxl2.ESC_ID_LAST) {
        this.device = frame[3];
        await this.br.write(srxl2.handshake(this.device, srxl2.BAUD_BIT_400K));
        await this.br.write(srxl2.handshake(srxl2.BROADCAST, 0));
        this.log(`ESC ${hex(this.device)}`);
      }
    } else if (frame[1] === srxl2.TELEMETRY && body.length >= 4) {
      this.lastTelemetry = this.clock();
      if (body[1] === TEXTGEN_SENSOR && this.screen.feed(body.subarray(1))) {
        this.lastChange = this.clock();
      }
    }
  }

  // Wait for an ESC to answer. An Avian with no master announces itself about twenty times a second after power-up
  // and whenever the flight controller stops talking to it, which is what opening INAV's passthrough does.
  async connect(timeout = 45000) {
    const start = this.clock();
    const end = start + timeout;
    let said = 0;
    while (this.device === null && this.clock() < end) {
      await this.pump(250);
      const waited = this.clock() - start;
      if (waited - said >= 5000) {
        said = waited;
        this.log(`waiting for the ESC, ${Math.round(waited / 1000)} s of ${Math.round(timeout / 1000)}`);
      }
    }
    if (this.device === null) {
      throw new MenuError(
        `no ESC answered on ${hex(srxl2.ESC_ID_FIRST)}..${hex(srxl2.ESC_ID_LAST)} after ` +
          `${Math.round(timeout / 1000)} s: check that it has power and that the signal wire reaches the right pad`,
      );
    }
    return this.device;
  }

  // wait for the screen to stop changing, then return it
  async settle(quiet = SETTLE, limit = 6000) {
    const end = this.clock() + limit;
    while (this.clock() < end) {
      await this.pump(150);
      if (this.screen.complete() && this.clock() - this.lastChange > quiet) {
        break;
      }
    }
    return this.screen;
  }

  async pulse(channel, value, settle = true) {
    this.channels[channel] = value;
    await this.pump(PULSE);
    this.channels[channel] = MID;
    await this.pump(RECENTRE);
    if (settle) {
      await this.settle();
    }
  }

  nextEntry() {
    return this.pulse(ELEVATOR, HIGH);
  }

  nextValue() {
    return this.pulse(AILERON, HIGH);
  }

  previousValue() {
    return this.pulse(AILERON, LOW);
  }

  // A prompting screen mentions entering; an open one has a cursor
  isOpen() {
    const text = [...this.screen.rows.values()].join(" ").toUpperCase();
    if (text.includes("ENTER MENU") || text.includes("HOLD 5-10")) {
      return false;
    }
    return this.screen.selected() !== null;
  }

  // The two-step stick hold that opens the menu, unless it is open already
  async enter(hold = STEP_HOLD) {
    await this.connect();
    await this.settle(400, 4000);
    if (this.isOpen()) {
      this.log("menu already open");
      return this.screen;
    }
    this.channels[ELEVATOR] = HIGH;
    this.channels[AILERON] = LOW;
    this.log("step 1: throttle low, elevator up, aileron left");
    await this.pump(hold);
    this.channels[AILERON] = HIGH;
    this.log("step 2: aileron right");
    await this.pump(hold);
    this.channels[ELEVATOR] = MID;
    this.channels[AILERON] = MID;
    await this.pump(500);
    await this.settle();
    if (!this.isOpen()) {
      const rows = [...this.screen.rows.keys()].sort((a, b) => a - b).map((r) => this.screen.rows.get(r));
      throw new MenuError(`the menu did not open; screen says: ${rows.join(" / ")}`);
    }
    return this.screen;
  }

  // Step through the whole list, collecting {name, value}; stops when the cursor stops moving or comes back to
  // where it started. onFound(name, value, count) is called as each entry appears: the walk takes most of a minute.
  async walk(limit = 40, onFound = null) {
    const found = [];
    const seen = new Set();
    for (let i = 0; i < limit; i++) {
      const here = this.screen.selected();
      if (here === null || seen.has(here.name)) {
        break;
      }
      seen.add(here.name);
      found.push(here);
      onFound?.(here.name, here.value, found.length);
      await this.nextEntry();
      const after = this.screen.selected();
      if (after !== null && after.name === here.name) {
        break; // the cursor refused to move: the end
      }
    }
    return found;
  }

  // put the cursor on a named parameter, from wherever it is
  async goto(name, limit = 40) {
    const want = name.trim().toUpperCase();
    for (let i = 0; i < limit; i++) {
      const here = this.screen.selected();
      if (here === null) {
        throw new MenuError("no cursor on screen");
      }
      if (here.name.toUpperCase() === want) {
        return here;
      }
      await this.nextEntry();
      const after = this.screen.selected();
      if (after !== null && after.name === here.name) {
        break; // end of the list, no wrap offered
      }
    }
    const offered = this.screen
      .entries()
      .map((e) => e.name)
      .join(", ");
    throw new MenuError(`no parameter called "${name}"; the screen offers ${offered}`);
  }

  // One step along a parameter's own list of values; returns the value the ESC now reports
  async bump(name, forward = true) {
    const here = await this.goto(name);
    const asked = this.clock();
    if (forward) {
      await this.nextValue();
    } else {
      await this.previousValue();
    }
    // rows kept from before the pulse would pass for the ESC's answer
    if (this.lastTelemetry <= asked + PULSE + RECENTRE) {
      throw new LinkLost(`the ESC did not report ${name} after the change: switch it off and on, then connect again`);
    }
    const after = this.screen.selected();
    if (after?.name !== here.name) {
      throw new MenuError(`the cursor left ${name} during the change; the screen shows ${after?.name ?? "no cursor"}`);
    }
    return after.value;
  }

  answering(within = 1500) {
    return this.device !== null && this.clock() - this.lastTelemetry < within;
  }

  // Trigger an entry with no value (EXIT W/ SAVE, DEFAULT/EXIT, EXIT). Leaving restarts the ESC, which announces
  // itself again for well under a second: the link is picked up again here inside that window, and counts only
  // once telemetry comes back, so the flight controller can take it over when the session ends.
  async activate(name) {
    await this.goto(name);
    this.log(`activating ${name}`);
    this.channels[AILERON] = HIGH;
    await this.pump(PULSE);
    this.channels[AILERON] = MID;
    await this.pump(250);

    // listen only: it announces by itself now, and a probe on the single wire can land on top of an announcement
    this.probing = false;
    let found = false;
    const end = this.clock() + 3000;
    try {
      while (!found && this.clock() < end) {
        this.device = null;
        while (this.device === null && this.clock() < end) {
          await this.pump(20);
        }
        if (this.device === null) {
          break;
        }
        const linked = this.clock();
        await this.pump(300);
        found = this.lastTelemetry > linked;
      }
    } finally {
      this.probing = true;
    }
    if (found) {
      // a full second of link before the session ends, as measured on the bench
      await this.pump(700);
    } else {
      this.log(`the ESC did not come back after ${name}: cycle its battery`);
    }
    return found;
  }
}
