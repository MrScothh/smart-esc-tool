// The two ways to the ESC's wire, ported from smart_esc_tool/transport/. Both expose the same surface:
//     write(bytes)   put bytes on the wire
//     poll()         -> [{tag, us, data}], whatever has arrived since the last call
//     setBaud(baud)  change the rate of the wire itself
//     reset()        drop what is in flight
//     close()
// Tags: "R" bytes from the ESC, "E" the echo of our own transmission, "I" a message from the adapter, "!" an error.
import { sleep } from "./serial_link.js";
import {
  FUNCTION_ESC_SRXL2_ID,
  MSP2_INAV_ESC_SRXL2_STATUS,
  MSP_SET_PASSTHROUGH,
  PASSTHROUGH_SERIAL_FUNCTION_ID,
  concat,
  find,
  msp2Request,
  mspRequest,
} from "./msp.js";

const SLIP_END = 0xc0;
const SLIP_ESC = 0xdb;
const SLIP_ESC_END = 0xdc;
const SLIP_ESC_ESC = 0xdd;
const now = () => performance.now();

function slip(payload) {
  const out = [SLIP_END];
  for (const b of payload) {
    if (b === SLIP_END) {
      out.push(SLIP_ESC, SLIP_ESC_END);
    } else if (b === SLIP_ESC) {
      out.push(SLIP_ESC, SLIP_ESC_ESC);
    } else {
      out.push(b);
    }
  }
  out.push(SLIP_END);
  return Uint8Array.from(out);
}

// Host side of the ESP32 SRXL2 wire adapter (esp32-bridge/): it owns the pad, the wire's rate and a microsecond clock
export class Esp32Bridge {
  static async open(link, usbBaud = 921600) {
    await link.open(usbBaud);
    await sleep(300); // boards that auto-reset on DTR
    link.discard();
    return new Esp32Bridge(link);
  }

  constructor(link) {
    this.link = link;
    this.acc = [];
    this.esc = false;
  }

  send(payload) {
    return this.link.write(slip(payload));
  }

  poll() {
    const events = [];
    for (let b of this.link.take()) {
      if (b === SLIP_END) {
        if (this.acc.length >= 5) {
          const a = this.acc;
          events.push({
            tag: String.fromCharCode(a[0]),
            us: (a[1] | (a[2] << 8) | (a[3] << 16) | (a[4] << 24)) >>> 0,
            data: Uint8Array.from(a.slice(5)),
          });
        }
        this.acc = [];
        this.esc = false;
        continue;
      }
      if (b === SLIP_ESC) {
        this.esc = true;
        continue;
      }
      if (this.esc) {
        b = b === SLIP_ESC_END ? SLIP_END : b === SLIP_ESC_ESC ? SLIP_ESC : b;
        this.esc = false;
      }
      this.acc.push(b);
    }
    return events;
  }

  identify() {
    return this.send([0x3f]); // "?"
  }

  setBaud(baud) {
    return this.send([0x42, baud & 0xff, (baud >> 8) & 0xff, (baud >> 16) & 0xff, (baud >>> 24) & 0xff]); // "B"
  }

  write(data) {
    return this.send([0x57, ...data]); // "W"
  }

  keepalive(periodMs, frame = []) {
    return this.send([0x4b, periodMs & 0xff, periodMs >> 8, ...frame]); // "K"
  }

  reportEcho(on = true) {
    return this.send([0x45, on ? 1 : 0]); // "E"
  }

  async reset() {
    await this.send([0x58]); // "X": drop everything in flight
    await sleep(50);
    this.poll();
  }

  async close() {
    try {
      await this.keepalive(0);
    } finally {
      await this.link.close();
    }
  }
}

// Through a flight controller running INAV: MSP_SET_PASSTHROUGH hands its Spektrum Smart ESC port to the USB link.
// The board mirrors the host's line rate onto that port, and leaves on +++ between seconds of silence.
export class InavPassthrough {
  // rates to try for MSP: a flight controller answers at the first, the ESP32 adapter (which can also act as one)
  // at the second
  static MSP_RATES = [115200, 921600];

  static async open(link, wireBaud = 115200) {
    await link.open(InavPassthrough.MSP_RATES[0]);
    const t = new InavPassthrough(link);
    await t.openPassthrough(wireBaud);
    return t;
  }

  constructor(link) {
    this.link = link;
    this.t0 = now();
    // whether the wire's rate follows this link's: a flight controller mirrors it, the adapter does not
    this.mirrorsBaud = true;
  }

  // true if a port was opened, false if refused, null if nothing answered at this rate
  async askPassthrough() {
    this.link.discard();
    await this.link.write(mspRequest(MSP_SET_PASSTHROUGH, [PASSTHROUGH_SERIAL_FUNCTION_ID, FUNCTION_ESC_SRXL2_ID]));
    const deadline = now() + 1000;
    let buf = new Uint8Array(0);
    while (now() < deadline) {
      buf = concat(buf, this.link.take());
      const i = find(buf, "$M>");
      if (i >= 0 && buf.length >= i + 6) {
        return buf[i + 5] !== 0;
      }
      await sleep(5);
    }
    return null;
  }

  async openPassthrough(wireBaud) {
    let answer = await this.askPassthrough();
    if (answer === null) {
      // opening a USB serial port can reset a board wired for it: wait that out, then try every rate again
      await sleep(1600);
      for (let attempt = 0; attempt < 2 && answer === null; attempt++) {
        for (const rate of InavPassthrough.MSP_RATES) {
          if (this.link.baudRate !== rate) {
            await this.link.reopen(rate);
            await sleep(150);
          }
          answer = await this.askPassthrough();
          if (answer !== null) {
            break;
          }
        }
      }
    }
    if (answer === null) {
      throw new Error("no reply to MSP_SET_PASSTHROUGH; is this an INAV board?");
    }
    if (answer === false) {
      throw new Error(
        "the board has no open port for Spektrum Smart ESC: assign one in the Ports tab, " +
          "set the motor protocol to SRXL2, and reboot",
      );
    }
    // a link that answered above 115200 is the adapter, whose USB rate is the link itself
    if (this.link.baudRate > 115200) {
      this.mirrorsBaud = false;
    } else {
      await this.setBaud(wireBaud);
    }
    this.link.discard();
  }

  async setBaud(baud) {
    if (!this.mirrorsBaud || this.link.baudRate === baud) {
      return;
    }
    await this.link.reopen(baud);
    await sleep(50); // the board mirrors the rate every 15 ms
  }

  write(data) {
    return this.link.write(Uint8Array.from(data));
  }

  // the echo of our own bytes comes back off the single wire and is simply passed up: the frame splitter resyncs on
  // the magic byte and the menu ignores what is not from the ESC
  poll() {
    const chunk = this.link.take();
    return chunk.length ? [{ tag: "R", us: Math.round((now() - this.t0) * 1000), data: chunk }] : [];
  }

  reportEcho() {}

  async reset() {
    this.link.discard();
  }

  async close() {
    try {
      // Hayes escape: a second of silence, +++, and a second of silence after it, which is when INAV leaves
      await sleep(1100);
      await this.link.write(new TextEncoder().encode("+++"));
      await sleep(1200);
    } finally {
      await this.link.close();
    }
  }
}

// After a session: does the flight controller have the ESC again? Leaving the ESC's menu restarts it, and it
// announces itself for under a second, so the answer is real either way and the aircraft must not fly on a guess.
// true once INAV reports the link, false if not within `wait` ms, null if the board did not answer or lacks the
// command.
export async function escLinked(link, wait = 4000) {
  await link.open(115200);
  let answered = false;
  try {
    const end = now() + wait;
    while (now() < end) {
      link.discard();
      await link.write(msp2Request(MSP2_INAV_ESC_SRXL2_STATUS));
      let buf = new Uint8Array(0);
      const deadline = now() + 300;
      while (now() < deadline) {
        buf = concat(buf, link.take());
        const i = find(buf, "$X>");
        if (i >= 0 && buf.length >= i + 10) {
          const size = buf[i + 6] | (buf[i + 7] << 8);
          if (size >= 2 && buf.length >= i + 8 + size) {
            answered = true;
            if (buf[i + 9]) {
              return true;
            }
            break;
          }
        }
        if (find(buf, "$X!") >= 0) {
          return null;
        }
        await sleep(10);
      }
      await sleep(200);
    }
  } finally {
    await link.close();
  }
  return answered ? false : null;
}
