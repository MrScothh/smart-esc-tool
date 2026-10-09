// The text page an Avian sends over SRXL2, reassembled: ported from smart_esc_tool/textgen.py, where the format is
// described. A title, then alternating rows, a parameter name and its value; the selected name starts with ">".
export const TEXTGEN_SENSOR = 0x0c;
export const CURSOR = ">";

function text(bytes) {
  return Array.from(bytes, (c) => (c >= 32 && c < 127 ? String.fromCharCode(c) : " "))
    .join("")
    .trimEnd();
}

export class Screen {
  constructor() {
    this.rows = new Map();
    this.changes = 0;
  }

  // One TextGen payload, the telemetry body from the sensor byte on: sensor, sID, row, characters. True if a row
  // changed.
  feed(payload) {
    if (payload.length < 4 || payload[0] !== TEXTGEN_SENSOR) {
      return false;
    }
    const row = payload[2];
    const line = text(payload.subarray(3));
    if (this.rows.get(row) === line) {
      return false;
    }
    this.rows.set(row, line);
    this.changes++;
    return true;
  }

  clear() {
    this.rows.clear();
  }

  title() {
    return (this.rows.get(0) ?? "").trim();
  }

  // enough rows for at least one name and its value
  complete() {
    return this.rows.size >= 3;
  }

  // [{name, value, selected}] in screen order, paired by position: values such as "CH7" look like names
  entries() {
    const order = [...this.rows.keys()].filter((r) => r >= 1).sort((a, b) => a - b);
    const out = [];
    for (let i = 0; i + 1 < order.length; i += 2) {
      const raw = this.rows.get(order[i]);
      const name = raw.trim();
      if (!name || name === this.title()) {
        continue;
      }
      out.push({
        name: name.replace(/^>+/, "").trim(),
        value: this.rows.get(order[i + 1]).trim(),
        selected: raw.trimStart().startsWith(CURSOR),
      });
    }
    return out;
  }

  // {name, value} under the cursor, or null
  selected() {
    const entry = this.entries().find((e) => e.selected);
    return entry ? { name: entry.name, value: entry.value } : null;
  }

  render() {
    return [...this.rows.keys()]
      .sort((a, b) => a - b)
      .map((r) => this.rows.get(r))
      .join("\n");
  }
}
