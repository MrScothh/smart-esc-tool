// A simulated Avian behind a transport, for the tests and the demo mode: it behaves the way avian_menu.py and
// textgen.py describe the real one. It announces itself while it has no master, links when a handshake addresses it,
// answers control data addressed to it with one TextGen row per frame, opens its menu on the two-step stick hold,
// and restarts after save, defaults or exit, coming back only if someone shakes hands within a short window.
import * as srxl2 from "./srxl2.js";

// The list and the window as docs/text-menu.md records them from an SPMXAE70C; where it gives no values, these are
// stand-ins. Defaults are the first value, except THRUST REV on CH7; BRAKE TYPE's fourth value is Reverse.
const PARAMETERS = [
  ["FLIGHT MODE", ["Fixed-wing", "Helicopter"]],
  ["BRAKE TYPE", ["Disabled", "Normal", "Proportional", "Reverse"]],
  ["BRAKE FORCE", ["0", "1", "2", "3", "4", "5", "6", "7"]],
  ["CUTOFF TYPE", ["Soft", "Hard"]],
  ["LIPO CELLS", ["Auto", "2S", "3S", "4S", "5S", "6S"]],
  ["CUTOFF VOLT", ["3.0V", "3.2V", "3.4V"]],
  ["BEC VOLTAGE", ["6.0V", "7.4V", "8.4V"]],
  ["STARTUP TIME", ["Normal", "Soft", "Very Soft"]],
  ["MOTOR ROTATE", ["Normal", "Reverse"]],
  ["ACTIVE FW", ["Disabled", "Enabled"]],
  ["GOV GAIN", ["Low", "Medium", "High"]],
  ["AR TIME", ["0.5s", "1.0s", "2.0s"]],
  ["RESTARTACCEL", ["Fast", "Medium", "Slow"]],
  ["THRUST REV", ["CH5", "CH6", "CH7", "CH8"]],
];
const DEFAULTS = PARAMETERS.map(([name]) => (name === "THRUST REV" ? 2 : 0));
const ACTIONS = ["EXIT W/ SAVE", "DEFAULT/EXIT", "EXIT"];
const WINDOW = 5; // entries on screen at once
const HOLD = 5000; // ms of stick hold for each entry step
const LINK_TIMEOUT = 250; // ms without a frame for it before it lets its master go

function level(value) {
  return value < 0x4000 ? "low" : value > 0xc000 ? "high" : "mid";
}

export class FakeAvian {
  constructor({ id = 0x40, clock = () => performance.now(), echo = false, announces = true } = {}) {
    this.id = id;
    this.clock = clock;
    this.echo = echo; // a single wire through a flight controller returns our own bytes
    this.announces = announces;
    this.out = [];
    this.inbox = new Uint8Array(0);
    this.saved = [...DEFAULTS];
    this.current = [...this.saved];
    this.drops = 0; // links lost to a gap in the feed, which a session must never cause
    this.powerOn();
  }

  powerOn(quietAfter = Infinity) {
    this.linked = false;
    this.lastHeard = this.clock();
    this.state = "step1";
    this.cursor = 0;
    this.held = null;
    this.sticks = { aileron: "mid", elevator: "mid", throttle: "low" };
    this.row = 0;
    this.nextAnnounce = this.clock();
    this.quietAt = this.clock() + quietAfter;
  }

  // --- the transport surface the menu uses ---
  async write(bytes) {
    if (this.echo) {
      this.out.push(...bytes);
    }
    const joined = new Uint8Array(this.inbox.length + bytes.length);
    joined.set(this.inbox);
    joined.set(bytes, this.inbox.length);
    const { frames, rest } = srxl2.split(joined);
    this.inbox = rest;
    for (const frame of frames) {
      this.receive(frame);
    }
  }

  poll() {
    const t = this.clock();
    this.checkLink(t);
    if (!this.linked && this.announces && t < this.quietAt && t >= this.nextAnnounce) {
      this.nextAnnounce = t + 50;
      this.send(srxl2.handshake(srxl2.BROADCAST, srxl2.BAUD_BIT_400K, this.id));
    }
    if (!this.out.length) {
      return [];
    }
    return [{ tag: "R", us: Math.round(t * 1000), data: Uint8Array.from(this.out.splice(0)) }];
  }

  async setBaud() {}
  async reset() {
    this.out = [];
  }
  reportEcho() {}
  async close() {}

  // --- the ESC ---
  send(frame) {
    this.out.push(...frame);
  }

  checkLink(t) {
    if (this.linked && t - this.lastHeard > LINK_TIMEOUT) {
      this.linked = false;
      this.drops++;
    }
  }

  receive(frame) {
    if (!srxl2.crcOk(frame)) {
      return;
    }
    const t = this.clock();
    this.checkLink(t);
    if (frame[1] === srxl2.HANDSHAKE && frame[4] === this.id && t < this.quietAt) {
      if (!this.linked) {
        this.send(srxl2.handshake(srxl2.BROADCAST, srxl2.BAUD_BIT_400K, this.id)); // a device answers a call
      }
      this.linked = true;
      this.lastHeard = t;
      return;
    }
    if (frame[1] === srxl2.CONTROL && this.linked && frame[4] === this.id) {
      this.lastHeard = t;
      this.sticksFrom(frame);
      this.reply();
    }
  }

  sticksFrom(frame) {
    const mask = frame[8] | (frame[9] << 8) | (frame[10] << 16) | (frame[11] << 24);
    const values = {};
    let at = 12;
    for (let ch = 0; ch < 32; ch++) {
      if (mask & (1 << ch)) {
        values[ch] = frame[at] | (frame[at + 1] << 8);
        at += 2;
      }
    }
    const next = {
      throttle: level(values[0] ?? 0x8000),
      aileron: level(values[1] ?? 0x8000),
      elevator: level(values[2] ?? 0x8000),
    };
    this.onSticks(this.sticks, next);
    this.sticks = next;
  }

  onSticks(before, now) {
    const t = this.clock();
    if (this.state === "step1" || this.state === "step2") {
      const wanted = this.state === "step1" ? "low" : "high";
      const holding = now.throttle === "low" && now.elevator === "high" && now.aileron === wanted;
      if (!holding) {
        this.held = null;
      } else if (this.held === null) {
        this.held = t;
      } else if (t - this.held >= HOLD) {
        this.state = this.state === "step1" ? "step2" : "menu";
        this.held = null;
      }
      return;
    }
    const entries = PARAMETERS.length + ACTIONS.length;
    // the list wraps: AvianMenu.walk stops when it comes back to the start, goto only ever moves forward
    if (before.elevator === "mid" && now.elevator === "high") {
      this.cursor = (this.cursor + 1) % entries;
    } else if (before.elevator === "mid" && now.elevator === "low") {
      this.cursor = (this.cursor + entries - 1) % entries;
    } else if (before.aileron === "mid" && now.aileron !== "mid") {
      if (this.cursor < PARAMETERS.length) {
        const options = PARAMETERS[this.cursor][1].length;
        const step = now.aileron === "high" ? 1 : options - 1;
        this.current[this.cursor] = (this.current[this.cursor] + step) % options;
      } else if (now.aileron === "high") {
        this.act(ACTIONS[this.cursor - PARAMETERS.length]);
      }
    }
  }

  act(action) {
    if (action === "EXIT W/ SAVE") {
      this.saved = [...this.current];
    } else if (action === "DEFAULT/EXIT") {
      this.saved = [...DEFAULTS];
      this.current = [...this.saved];
    } else {
      this.current = [...this.saved];
    }
    // restarts, announces itself for under a second, and stays off the bus if nobody answers
    this.powerOn(900);
  }

  screenRows() {
    if (this.state !== "menu") {
      // the screen narrates the entry, with as many rows as the menu: the ESC always sends a whole page
      const aileron = this.state === "step1" ? "Left Aile" : "Right Aile";
      return ["  Avian Prog", "Low Throt", "Up Elev", aileron, "Hold 5-10sec", ...Array(6).fill("")];
    }
    const entries = [
      ...PARAMETERS.map(([name, options], i) => [name, options[this.current[i]]]),
      ...ACTIONS.map((name) => [name, ""]),
    ];
    const first = Math.max(0, Math.min(this.cursor - 1, entries.length - WINDOW));
    const rows = ["  Avian Prog"];
    for (let i = first; i < first + WINDOW; i++) {
      const [name, value] = entries[i];
      rows.push(`${i === this.cursor ? ">" : " "}${name}`, value.padStart(13));
    }
    return rows;
  }

  reply() {
    const rows = this.screenRows();
    this.row = (this.row + 1) % rows.length;
    const text = rows[this.row].padEnd(13).slice(0, 13);
    const body = [srxl2.MAGIC, srxl2.TELEMETRY, 0, srxl2.OUR_DEVICE_ID, 0x0c, 0, this.row];
    for (const c of text) {
      body.push(c.charCodeAt(0));
    }
    this.send(srxl2.seal(Uint8Array.from(body)));
  }
}
