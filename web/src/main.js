// The window: draws what the worker reports, sends it one job at a time, and never touches the serial port. Every
// value shown is the one the ESC reported after a change, never the one that was asked for.
import "@fluentui/web-components/button.js";
import "@fluentui/web-components/divider.js";
import "@fluentui/web-components/dropdown.js";
import "@fluentui/web-components/listbox.js";
import "@fluentui/web-components/message-bar.js";
import "@fluentui/web-components/option.js";
import "@fluentui/web-components/progress-bar.js";
import "@fluentui/web-components/text.js";
import { setTheme } from "@fluentui/web-components";
import { webDarkTheme, webLightTheme } from "@fluentui/tokens";
import chevronLeft from "@fluentui/svg-icons/icons/chevron_left_20_regular.svg?raw";
import chevronRight from "@fluentui/svg-icons/icons/chevron_right_20_regular.svg?raw";
import connectIcon from "@fluentui/svg-icons/icons/plug_connected_20_regular.svg?raw";
import historyIcon from "@fluentui/svg-icons/icons/history_20_regular.svg?raw";
import saveIcon from "@fluentui/svg-icons/icons/save_20_regular.svg?raw";
import usbIcon from "@fluentui/svg-icons/icons/usb_plug_20_regular.svg?raw";

const ICONS = { connect: connectIcon, history: historyIcon, save: saveIcon, usb: usbIcon };
const $ = (id) => document.getElementById(id);
const demo = new URLSearchParams(location.search).has("demo");

// light and dark follow the system, as the desktop app does
const dark = matchMedia("(prefers-color-scheme: dark)");
const applyTheme = () => setTheme(dark.matches ? webDarkTheme : webLightTheme);
dark.addEventListener("change", applyTheme);
applyTheme();

for (const slot of document.querySelectorAll("[data-icon]")) {
  slot.innerHTML = ICONS[slot.dataset.icon];
}

const serial = "serial" in navigator;
const state = { busy: false, menuOpen: false, device: null, ports: [], cards: new Map() };
const worker = new Worker(new URL("./worker.js", import.meta.url), { type: "module" });
// busy at once, not when the worker says so: a double click would otherwise queue the job twice
const send = (job) => {
  setBusy(true);
  worker.postMessage(job);
};

// --- drawing ---
function setBusy(busy) {
  state.busy = busy;
  $("progress").classList.toggle("busy", busy);
  $("connect").disabled = busy || !(serial || demo);
  // a scan ends the session, and unsaved changes with it
  $("add-port").disabled = busy || !serial || state.menuOpen;
  for (const id of ["save", "defaults", "leave"]) {
    $(id).disabled = busy || !state.menuOpen;
  }
  for (const card of state.cards.values()) {
    card.previous.disabled = card.next.disabled = busy;
  }
}

function say(text, tone = "info") {
  const status = $("status");
  status.textContent = text;
  status.className = `status ${tone}`;
}

function notify(title, text, intent) {
  const bar = document.createElement("fluent-message-bar");
  bar.setAttribute("intent", intent);
  bar.innerHTML = `<b></b> <span></span>`;
  bar.querySelector("b").textContent = title;
  bar.querySelector("span").textContent = text;
  $("notices").append(bar);
  setTimeout(() => bar.remove(), 5000);
}

function showPorts(ports) {
  state.ports = ports;
  const list = $("port-list");
  list.replaceChildren();
  const usable = ports.find((p) => p.usable);
  for (const port of ports) {
    const option = document.createElement("fluent-option");
    option.value = String(port.index);
    option.textContent = port.label;
    // selected on the option itself: the dropdown does not know its options yet when they are appended
    option.toggleAttribute("selected", port === usable);
    list.append(option);
  }
  $("ports").placeholder = ports.length ? "Choose a board" : "Add a port to begin";
}

// as Python's str.title() in the desktop app: "BEC VOLTAGE" -> "Bec Voltage"
const title = (name) => name.toLowerCase().replace(/(^|[^a-z])([a-z])/g, (_, before, c) => before + c.toUpperCase());

function stepButton(icon, tip, onClick) {
  const button = document.createElement("fluent-button");
  button.setAttribute("appearance", "transparent");
  button.setAttribute("icon-only", "");
  button.title = tip;
  button.innerHTML = icon;
  button.addEventListener("click", onClick);
  return button;
}

function addEntry(name, value) {
  if (state.cards.has(name)) {
    setValue(name, value);
    return;
  }
  const row = document.createElement("div");
  row.className = "card";
  const label = document.createElement("fluent-text");
  label.className = "name";
  label.textContent = title(name);
  const shown = document.createElement("fluent-text");
  shown.className = "value";
  shown.textContent = value || "-";
  const card = { row, shown, initial: value, modified: false };
  card.previous = stepButton(chevronLeft, "Previous value", () => step(name, false));
  card.next = stepButton(chevronRight, "Next value", () => step(name, true));
  card.previous.disabled = card.next.disabled = state.busy;
  row.append(label, card.previous, shown, card.next);
  $("cards").append(row);
  state.cards.set(name, card);
}

function setValue(name, value) {
  const card = state.cards.get(name);
  if (!card) {
    return;
  }
  card.shown.textContent = value || "-";
  card.modified = (value ?? "") !== (card.initial ?? "");
  card.shown.classList.toggle("modified", card.modified);
  const n = [...state.cards.values()].filter((c) => c.modified).length;
  $("unsaved").textContent = n === 0 ? "" : n === 1 ? "1 change not saved" : `${n} changes not saved`;
}

function showEntries() {
  state.cards.clear();
  $("cards").replaceChildren();
  $("unsaved").textContent = "";
  $("group").hidden = false;
  $("empty").hidden = true;
}

function showDisconnected(text) {
  state.cards.clear();
  $("cards").replaceChildren();
  $("unsaved").textContent = "";
  $("group").hidden = true;
  $("empty").hidden = false;
  $("empty-text").textContent = text;
}

// --- events ---
function step(name, forward) {
  if (state.busy) {
    return;
  }
  say(`${title(name)}: asking the ESC for the ${forward ? "next" : "previous"} value`);
  send({ job: "bump", name, forward });
}

function action(name) {
  if (!state.busy) {
    send({ job: "action", name });
  }
}

$("connect").addEventListener("click", () => {
  const value = $("ports").value;
  const port = value ? state.ports.find((p) => p.index === Number(value)) : null;
  if (!port) {
    send({ job: "scan" });
    return;
  }
  if (!port.usable) {
    notify("Nothing answered", "That port did not answer a question only the right board can.", "warning");
    return;
  }
  state.menuOpen = false;
  say("Connecting");
  send(port.kind === "demo" ? { job: "demo" } : { job: "connect", index: port.index, kind: port.kind });
});

// a page may only open ports a person has picked in the browser's own chooser
$("add-port").addEventListener("click", async () => {
  try {
    await navigator.serial.requestPort();
    send({ job: "scan" });
  } catch {
    // the chooser was closed without picking
  }
});

$("save").addEventListener("click", () => action("EXIT W/ SAVE"));
$("defaults").addEventListener("click", () => action("DEFAULT/EXIT"));
$("leave").addEventListener("click", () => action("EXIT"));

worker.onmessage = ({ data: m }) => {
  switch (m.type) {
    case "status":
      say(m.text, m.tone);
      if (m.tone === "bad") {
        notify("Stopped", m.text, "error");
      }
      break;
    case "ports":
      showPorts(demo ? [...m.ports, { index: -1, kind: "demo", usable: true, label: "Simulated ESC (demo)" }] : m.ports);
      break;
    case "busy":
      setBusy(m.busy);
      break;
    case "linked":
      state.device = `ESC 0x${m.device.toString(16).toUpperCase()}`;
      $("subtitle").textContent = `${state.device} answered`;
      break;
    case "entriesBegin":
      showEntries();
      break;
    case "entry":
      addEntry(m.name, m.value);
      break;
    case "entriesEnd":
      state.menuOpen = true;
      $("subtitle").textContent = `${state.device}, menu open`;
      setBusy(state.busy);
      notify("Read from the ESC", `${m.count} parameters. A change is kept only when you press Save to ESC.`, "success");
      break;
    case "value":
      setValue(m.name, m.value);
      break;
    case "closed":
      state.menuOpen = false;
      setBusy(state.busy);
      $("subtitle").textContent = "Spektrum Avian over SRXL2";
      if (m.linked === true) {
        say("The flight controller has the ESC again", "good");
        showDisconnected("The ESC has left its menu and the flight controller has it again. Press Connect to go back in.");
      } else if (m.linked === false) {
        say("The flight controller does not see the ESC", "bad");
        notify("Switch the ESC off and on", "INAV will not arm until the ESC is back.", "error");
        showDisconnected(
          "The ESC has left its menu, but the flight controller does not see it. " +
            "Switch the ESC off and on before flying: INAV will not arm until you do.",
        );
      } else if (m.name) {
        showDisconnected("The ESC has left its menu. Switch it off and on, then press Connect to go back in.");
      } else {
        showDisconnected("Not connected to the ESC. Switch it off and on, then press Connect to go back in.");
      }
      break;
  }
};

worker.onerror = (event) => {
  say(`The part of the page that talks to the port stopped: ${event.message}. Reload the page.`, "bad");
  notify("Stopped", "Reload the page to start again.", "error");
};

if (!serial) {
  showDisconnected(
    "This browser cannot reach serial ports. Open this page in Chrome or Edge on a computer" +
      (demo ? "; the simulated ESC still works." : "."),
  );
  setBusy(false);
  if (demo) {
    showPorts([{ index: -1, kind: "demo", usable: true, label: "Simulated ESC (demo)" }]);
  }
} else {
  setBusy(true);
  send({ job: "scan" });
}
