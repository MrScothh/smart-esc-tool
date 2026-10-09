// Owns the wire, as the desktop app's Worker thread does (smart_esc_tool/gui.py): the page sends one job at a time and
// draws what comes back; it never touches the serial port. Between jobs the ESC's link is still fed, because the ESC
// drops it after about a quarter of a second without a frame.
import { AvianMenu, LinkLost, MenuError } from "./avian_menu.js";
import { FakeAvian } from "./fake_avian.js";
import { MSP_FC_VARIANT, MSP_FC_VERSION, concat, mspReply, mspRequest } from "./msp.js";
import { SerialLink, sleep } from "./serial_link.js";
import * as srxl2 from "./srxl2.js";
import { Esp32Bridge, InavPassthrough, escLinked } from "./transports.js";

const ACTIONS = ["EXIT W/ SAVE", "DEFAULT/EXIT", "EXIT"];
const PROBE_LIMIT = 6000; // ms per port, what the desktop app allows a whole scan

// only to decide which question to ask first, never as an answer
const LIKELY_BRIDGE = new Set(["10c4:ea60", "1a86:7523", "1a86:55d4", "303a:1001"]);
const USB_NAMES = {
  "0483:5740": "STM32 USB",
  "10c4:ea60": "CP210x",
  "1a86:7523": "CH340",
  "1a86:55d4": "CH9102",
  "303a:1001": "ESP32-S3 USB",
};

let ports = []; // what the last scan found, the page refers to them by index
let session = null; // {transport, menu, kind, port}
const jobs = [];
let wake = null;

const post = (message) => postMessage(message);
const say = (text, tone = "info") => post({ type: "status", text, tone });

function usbId(port) {
  const { usbVendorId: v, usbProductId: p } = port.getInfo();
  return v === undefined ? "" : `${v.toString(16).padStart(4, "0")}:${p.toString(16).padStart(4, "0")}`;
}

async function askMsp(link, cmd, timeout = 600) {
  link.discard();
  await link.write(mspRequest(cmd));
  const deadline = performance.now() + timeout;
  let buf = new Uint8Array(0);
  while (performance.now() < deadline) {
    buf = concat(buf, link.take());
    const payload = mspReply(buf);
    if (payload) {
      return payload;
    }
    await sleep(10);
  }
  return null;
}

async function probeFlightController(link) {
  try {
    await link.open(115200);
    await sleep(150); // some boards drop the first bytes
    const variant = await askMsp(link, MSP_FC_VARIANT);
    if (!variant?.length) {
      return null;
    }
    let name = new TextDecoder().decode(variant).trim();
    const version = await askMsp(link, MSP_FC_VERSION);
    if (version?.length >= 3) {
      name += ` ${version[0]}.${version[1]}.${version[2]}`;
    }
    return { kind: name.startsWith("INAV") ? "inav" : "betaflight", detail: name };
  } catch {
    return null;
  } finally {
    await link.close();
  }
}

async function probeAdapter(link) {
  try {
    const bridge = await Esp32Bridge.open(link);
    await bridge.reset();
    await bridge.identify();
    const deadline = performance.now() + 1200;
    while (performance.now() < deadline) {
      for (const event of bridge.poll()) {
        const text = new TextDecoder().decode(event.data).trim();
        if (event.tag === "I" && /srxl2|bridge/i.test(text)) {
          return { kind: "esp32", detail: text };
        }
      }
      await sleep(20);
    }
    return null;
  } catch {
    return null;
  } finally {
    await link.close();
  }
}

// a USB serial port names its bridge chip, nothing about what is behind it: ask a question only the right device
// can answer, a flight controller first unless the chip suggests the adapter
async function probe(port) {
  const order = LIKELY_BRIDGE.has(usbId(port))
    ? [probeAdapter, probeFlightController]
    : [probeFlightController, probeAdapter];
  const asking = { link: null, late: false };
  const answer = (async () => {
    for (const ask of order) {
      if (asking.late) {
        return null;
      }
      asking.link = new SerialLink(port);
      const found = await ask(asking.link);
      if (found) {
        return found;
      }
    }
    return null;
  })();
  // a device that stops draining must not hold the whole scan
  let timer;
  const limit = new Promise((resolve) => {
    timer = setTimeout(() => {
      asking.late = true;
      resolve(null);
    }, PROBE_LIMIT);
  });
  const found = await Promise.race([answer, limit]);
  clearTimeout(timer);
  if (asking.late) {
    await asking.link?.abort();
  }
  return found;
}

function describe(port, found, index) {
  const chip = USB_NAMES[usbId(port)] ?? (usbId(port) || "serial port");
  if (!found) {
    return { index, kind: null, usable: false, label: `${chip}: nothing answered` };
  }
  const label = found.kind === "esp32" ? `SRXL2 adapter (${chip})` : `${found.detail} (${chip})`;
  return { index, kind: found.kind, usable: found.kind === "inav" || found.kind === "esp32", label };
}

const jobsByName = {
  async scan() {
    await closeSession();
    say("Looking at the ports this browser may use");
    ports = (await navigator.serial?.getPorts()) ?? [];
    const results = await Promise.all(ports.map((port) => probe(port)));
    const list = ports.map((port, i) => describe(port, results[i], i));
    list.sort((a, b) => (a.kind === "inav" ? 0 : a.kind === "esp32" ? 1 : 2) - (b.kind === "inav" ? 0 : b.kind === "esp32" ? 1 : 2));
    post({ type: "ports", ports: list });
    say(list.some((p) => p.usable) ? "" : "No flight controller and no adapter answered", list.length ? "warn" : "info");
  },

  async connect({ index, kind }) {
    await closeSession();
    const port = ports[index];
    say("Opening the port");
    const link = new SerialLink(port);
    let transport;
    try {
      transport = kind === "inav" ? await InavPassthrough.open(link) : await Esp32Bridge.open(link);
    } catch (error) {
      await link.close();
      throw error;
    }
    await startSession(transport, kind, port);
  },

  async demo() {
    await closeSession();
    await startSession(new FakeAvian({ announces: true }), "demo", null);
  },

  async bump({ name, forward }) {
    const value = await menuOf().bump(name, forward);
    post({ type: "value", name, value });
    say(`${title(name)} is now ${value}`);
  },

  async action({ name }) {
    const { kind, port } = session ?? {};
    let back;
    try {
      back = await menuOf().activate(name);
    } catch (error) {
      await closeSession();
      throw error;
    }
    await closeSession(true);
    say(
      name === "EXIT W/ SAVE"
        ? "Saved. The ESC will keep these settings"
        : name === "DEFAULT/EXIT"
          ? "Factory defaults restored"
          : "Left the menu without writing anything",
      name === "EXIT" ? "warn" : "good",
    );
    if (!back) {
      say(`The ESC did not come back after ${name}: switch it off and on`, "bad");
    }
    let linked = null;
    if (kind === "inav") {
      // leaving restarts the ESC; whether the flight controller caught it again is something to ask, not assume
      say("Asking the flight controller whether it has the ESC again");
      try {
        linked = await escLinked(new SerialLink(port));
      } catch (error) {
        say(`Could not ask the flight controller: ${error.message}`, "warn");
      }
    }
    post({ type: "closed", name, linked });
  },
};

function menuOf() {
  if (!session) {
    throw new MenuError("No ESC is connected: press Connect");
  }
  return session.menu;
}

// as Python's str.title() in the desktop app: "BEC VOLTAGE" -> "Bec Voltage"
const title = (name) => name.toLowerCase().replace(/(^|[^a-z])([a-z])/g, (_, before, c) => before + c.toUpperCase());

async function startSession(transport, kind, port) {
  await transport.setBaud(srxl2.BAUD_LOW);
  await transport.reset();
  transport.reportEcho(false);
  const menu = new AvianMenu(transport, { log: (m) => say(m) });
  session = { transport, menu, kind, port };
  // a session that did not get as far as the menu must not keep the port, nor INAV in passthrough
  try {
    say("Waiting for the ESC. Switch it on now if it is not already", "warn");
    await menu.connect();
    post({ type: "linked", device: menu.device });
    say(`ESC 0x${menu.device.toString(16).toUpperCase()} answered`, "good");
    say("Opening the ESC's menu, about twenty seconds");
    await menu.enter();
    say("Reading the ESC's parameters");
    post({ type: "entriesBegin" });
    const entries = await menu.walk(40, (name, value, n) => {
      if (!ACTIONS.includes(name)) {
        post({ type: "entry", name, value });
      }
      say(`Read ${n} so far. ${title(name)} is ${value || "-"}`);
    });
    const count = entries.filter((e) => !ACTIONS.includes(e.name)).length;
    post({ type: "entriesEnd", count });
    say(`${count} parameters, all read from the ESC`, "good");
  } catch (error) {
    await closeSession();
    throw error;
  }
}

// quiet when the caller tells the page itself how the session ended
async function closeSession(quiet = false) {
  if (!session) {
    return;
  }
  const { transport } = session;
  session = null;
  try {
    await transport.close();
  } catch {
    // the port may already be gone
  }
  if (!quiet) {
    post({ type: "closed", linked: null });
  }
}

const describeError = (error) => (error instanceof MenuError ? error.message : `${error.name}: ${error.message}`);

async function run(job) {
  post({ type: "busy", busy: true });
  try {
    await jobsByName[job.job](job);
  } catch (error) {
    say(describeError(error), "bad");
    // a refused request leaves the menu as it was; an ESC that stopped answering or a port error does not
    if (!(error instanceof MenuError) || error instanceof LinkLost || !session?.menu.answering()) {
      await closeSession();
    }
  } finally {
    post({ type: "busy", busy: false });
  }
}

// keep the ESC's link while a person reads the screen, and notice when it is gone
async function idle() {
  try {
    await session.menu.pump(50);
    if (!session.menu.answering()) {
      throw new MenuError("The ESC stopped answering: switch it off and on, then press Connect");
    }
  } catch (error) {
    say(describeError(error), "bad");
    await closeSession();
  }
}

onmessage = (event) => {
  jobs.push(event.data);
  wake?.();
};

for (;;) {
  if (jobs.length) {
    await run(jobs.shift());
  } else if (session?.menu.device != null) {
    await idle();
  } else {
    await new Promise((resolve) => {
      wake = resolve;
    });
    wake = null;
  }
}
