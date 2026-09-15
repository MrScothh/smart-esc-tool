"""The text page an Avian sends over SRXL2, reassembled.

Spektrum's own documentation says an Avian can be configured "directly from your
Spektrum DX, iX or NX transmitter", by scrolling to the last telemetry page and
navigating with the aileron and elevator. That page is TextGen: ordinary
telemetry frames carrying one screen row each.

    telemetry body:  dest, sensor 0x0C, sID, row, thirteen characters

One row per frame means a whole screen takes a moment to arrive and is briefly
inconsistent while it does, so a reader has to wait for it to settle rather than
act on the first frame that mentions the row it cares about.

The screen itself is a title and then alternating rows: a parameter name, then
its current value, right-aligned. Exactly one name carries a `>` in the first
column, and that is the cursor.

      Avian Prog
     >FLIGHT MODE
        Fixed-wing
      BRAKE TYPE
          Disabled

Reading the value back is the part that stick programming cannot do at all, and
it is the reason this page is worth the trouble.
"""

TEXTGEN_SENSOR = 0x0C
CURSOR = ">"


class Screen(object):
    """Rows as they arrive, and the settings they add up to."""

    def __init__(self):
        self.rows = {}
        self.changes = 0            # how many rows have ever changed

    # ------------------------------------------------------------------ input
    def feed(self, payload):
        """Take one TextGen payload (sID, row, text). True if anything changed.

        `payload` is the telemetry body from the sensor byte onward, so
        payload[0] is the sensor id and payload[1] the row number. Getting this
        offset wrong puts every row in slot zero, where they overwrite each
        other and the screen looks like it is flickering between unrelated
        phrases.
        """
        if len(payload) < 4 or payload[0] != TEXTGEN_SENSOR:
            return False
        row = payload[2]
        text = "".join(chr(c) if 32 <= c < 127 else " " for c in payload[3:])
        text = text.rstrip()
        if self.rows.get(row) == text:
            return False
        self.rows[row] = text
        self.changes += 1
        return True

    def clear(self):
        self.rows.clear()

    # ----------------------------------------------------------------- output
    def title(self):
        return self.rows.get(0, "").strip()

    def complete(self):
        """Enough rows for at least one name and its value."""
        return len(self.rows) >= 3

    def entries(self):
        """[(name, value, selected)] in screen order.

        Rows after the title alternate name, value. Pairing by position rather
        than by guessing from the text keeps values like `CH7` or `Surge SW`,
        which look like names, on the right side of the pair.
        """
        order = sorted(r for r in self.rows if r >= 1)
        out = []
        for i in range(0, len(order) - 1, 2):
            name_row, value_row = order[i], order[i + 1]
            raw = self.rows[name_row]
            name = raw.strip()
            if not name or name == self.title():
                continue
            out.append((name.lstrip(CURSOR).strip(),
                        self.rows[value_row].strip(),
                        raw.lstrip().startswith(CURSOR) or raw.startswith(CURSOR)))
        return out

    def selected(self):
        """(name, value) under the cursor, or None."""
        for name, value, sel in self.entries():
            if sel:
                return name, value
        return None

    def value_of(self, name):
        want = name.strip().upper()
        for n, v, _ in self.entries():
            if n.upper() == want:
                return v
        return None

    def render(self):
        return "\n".join(self.rows[r] for r in sorted(self.rows))
