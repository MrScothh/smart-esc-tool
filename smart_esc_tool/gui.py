"""A window for configuring a Spektrum Avian, through a flight controller.

The ESC keeps its own menu and will not hold a link for longer than a quarter of
a second without a frame, so the wire has to be fed continuously while a person
reads the screen and decides what to change. That rules out doing any of it on
the interface thread: a worker owns the transport and the menu, takes one
instruction at a time from a queue, and posts back what the ESC now says. The
window only ever draws what came back.

Every value shown here was read from the ESC after the change, never assumed
from what was asked for.

The widgets are drawn rather than borrowed. Tk's own buttons and frames are flat
rectangles with a system border, and a window made of them looks like a form;
what follows is a small set of canvas widgets - rounded, with hover and pressed
states and a shadow under each card - because depth is what tells a person which
things are surfaces and which are controls.
"""

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import font as tkfont

from . import discover
from .avian_menu import AvianMenu, MenuError
from .transport import open_transport

# One palette, in one place. Everything below refers to these by name so a
# change of mind costs one line rather than forty.
BG = "#10131a"
CARD = "#1a1f29"
CARD_HI = "#20263223"
EDGE = "#2a3140"
TEXT = "#eef2f8"
DIM = "#8b96a9"
FAINT = "#5d6779"
ACCENT = "#3a82f6"
ACCENT_HI = "#5a9bff"
GOOD = "#3fbd7f"
WARN = "#e8b25c"
BAD = "#e46a6a"
SHADOW = "#0b0e14"

HEAD_TOP = "#1d2941"
HEAD_BOTTOM = "#151a24"

ACTIONS = ("EXIT W/ SAVE", "DEFAULT/EXIT", "EXIT")


def resource(name):
    """A file shipped next to the code, or unpacked beside a frozen binary."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidate = os.path.join(base, name)
        if os.path.exists(candidate):
            return candidate
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, name),
                 os.path.join(os.path.dirname(here), "packaging", name)):
        if os.path.exists(path):
            return path
    return None


def round_rect(canvas, x0, y0, x1, y1, r, **kw):
    """A rounded rectangle, as a smoothed polygon.

    Tk has no such primitive. Doubling each corner point and asking for a
    smoothed polygon gives curves that stay put when the widget is resized,
    which arcs stitched to lines do not.
    """
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return canvas.create_polygon(pts, smooth=True, splinesteps=24, **kw)


def fit_text(text, font, width):
    """Shorten text until it fits, with an ellipsis. Never loops forever.

    The first version of this shrank the string while it was too wide, which is
    fine until the available width is negative - which it is for one frame,
    before a freshly packed widget has been given its size. Then the condition
    can never be satisfied, and because the last character is an ellipsis the
    string stops getting shorter: the interface thread spins and the window
    stops responding. Both guards below exist for that.
    """
    if not text:
        return text
    if width <= 0:
        return text
    if font.measure(text) <= width:
        return text
    cut = text
    while len(cut) > 1 and font.measure(cut + "…") > width:
        cut = cut[:-1]
    return cut + "…"


def mix(a, b, t):
    """Blend two #rrggbb colours."""
    pa = tuple(int(a[i:i + 2], 16) for i in (1, 3, 5))
    pb = tuple(int(b[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(
        int(round(x + (y - x) * t)) for x, y in zip(pa, pb))


# ------------------------------------------------------------------- widgets
class RoundButton(tk.Canvas):
    """A button that knows it is being pointed at."""

    def __init__(self, parent, text, command, kind="ghost", width=None,
                 height=36, font=None, bg=BG):
        self.font = font or tkfont.Font(family="Segoe UI", size=10)
        w = width or (self.font.measure(text) + 40)
        tk.Canvas.__init__(self, parent, width=w, height=height, bg=bg,
                           highlightthickness=0, bd=0)
        self.command = command
        self.kind = kind
        self.text = text
        self._enabled = True
        self._hover = False
        self._down = False
        # Non chiamarli _w e _h: tkinter usa gia' self._w per il percorso Tk
        # del widget, e sovrascriverlo fa cercare un comando che non esiste.
        self._box_w, self._box_h = w, height
        self._draw()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _palette(self):
        if not self._enabled:
            return CARD, FAINT, EDGE
        if self.kind == "primary":
            fill = ACCENT_HI if self._hover else ACCENT
            return fill, "#0b1220", fill
        if self.kind == "good":
            fill = mix(GOOD, "#ffffff", 0.12) if self._hover else GOOD
            return fill, "#07140d", fill
        if self.kind == "danger":
            fill = mix(BAD, "#ffffff", 0.12) if self._hover else BAD
            return fill, "#1a0a0a", fill
        fill = mix(CARD, "#ffffff", 0.07) if self._hover else CARD
        return fill, TEXT, EDGE

    def _draw(self):
        self.delete("all")
        fill, fg, edge = self._palette()
        dy = 1 if self._down else 0
        r = min(self._box_h // 2, 14)
        if self._enabled and not self._down:
            round_rect(self, 1, 3, self._box_w - 1, self._box_h, r,
                       fill=SHADOW, outline="")
        round_rect(self, 1, 1 + dy, self._box_w - 1, self._box_h - 2 + dy, r,
                   fill=fill, outline=edge)
        self.create_text(self._box_w / 2, self._box_h / 2 + dy, text=self.text,
                         fill=fg, font=self.font)

    def configure_state(self, enabled):
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()

    def _on_enter(self, _):
        self._hover = True
        self._draw()

    def _on_leave(self, _):
        self._hover = self._down = False
        self._draw()

    def _on_press(self, _):
        if self._enabled:
            self._down = True
            self._draw()

    def _on_release(self, _):
        was = self._down
        self._down = False
        self._draw()
        if was and self._enabled and self.command:
            self.command()


class ParamRow(tk.Canvas):
    """One parameter: its name, its value, and a step either way."""

    HEIGHT = 56

    def __init__(self, parent, name, value, on_step, fonts):
        tk.Canvas.__init__(self, parent, height=self.HEIGHT, bg=BG,
                           highlightthickness=0, bd=0)
        self.name = name
        self.value = value
        self.on_step = on_step
        self.fonts = fonts
        self._enabled = True
        self._hover = None
        self._changed = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))
        self.bind("<ButtonRelease-1>", self._on_click)

    # -- geometry --------------------------------------------------------
    def _zones(self):
        """Right to left: step up, the value, step down.

        A widget that has just been packed reports a width of one pixel until
        Tk has laid it out, and every position here would come out negative.

        The value needs real room - "Fixed-wing" and "Auto Calc" are as much a
        value as "7" - and a field too narrow for them is worse than useless,
        because the text is clipped exactly where the difference between two
        options usually lives.
        """
        w = self.winfo_width()
        if w < 320:
            w = 600
        rr = w - 26
        plus = (rr - 34, 11, rr, 45)
        value = (plus[0] - 148, 11, plus[0] - 8, 45)
        minus = (value[0] - 42, 11, value[0] - 8, 45)
        return {"plus": plus, "value": value, "minus": minus}

    def _hit(self, x, y):
        for key, (x0, y0, x1, y1) in self._zones().items():
            if key != "value" and x0 <= x <= x1 and y0 <= y <= y1:
                return key
        return None

    # -- drawing ---------------------------------------------------------
    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        if w < 320:
            w = 600
        z = self._zones()

        round_rect(self, 8, 6, w - 8, self.HEIGHT - 2, 12, fill=SHADOW, outline="")
        body = mix(CARD, "#ffffff", 0.05) if self._hover else CARD
        round_rect(self, 8, 4, w - 8, self.HEIGHT - 4, 12, fill=body, outline=EDGE)

        if self._changed:
            self.create_rectangle(9, 16, 12, self.HEIGHT - 16,
                                  fill=ACCENT, outline="")

        name = fit_text(self.name.title(), self.fonts["body"],
                        z["minus"][0] - 28 - 16)
        self.create_text(28, self.HEIGHT / 2, text=name, anchor="w",
                         fill=TEXT if self._enabled else FAINT,
                         font=self.fonts["body"])

        vx0, vy0, vx1, vy1 = z["value"]
        round_rect(self, vx0, vy0, vx1, vy1, 9,
                   fill=mix(CARD, "#000000", 0.28), outline="")
        font = self.fonts["value"]
        text = fit_text(self.value or "-", font, (vx1 - vx0) - 14)
        self.create_text((vx0 + vx1) / 2, self.HEIGHT / 2, text=text, anchor="c",
                         fill=ACCENT_HI if self._enabled else FAINT, font=font)

        for key, glyph in (("minus", "‹"), ("plus", "›")):
            x0, y0, x1, y1 = z[key]
            on = self._enabled
            fill = mix(CARD, "#ffffff", 0.14) if (self._hover == key and on) else \
                mix(CARD, "#ffffff", 0.04)
            round_rect(self, x0, y0, x1, y1, 9, fill=fill,
                       outline=ACCENT if (self._hover == key and on) else EDGE)
            self.create_text((x0 + x1) / 2, (y0 + y1) / 2 - 1, text=glyph,
                             fill=TEXT if on else FAINT, font=self.fonts["glyph"])

    # -- behaviour -------------------------------------------------------
    def _set_hover(self, key):
        if key != self._hover:
            self._hover = key
            self.configure(cursor="hand2" if key and self._enabled else "arrow")
            self._draw()

    def _on_motion(self, event):
        self._set_hover(self._hit(event.x, event.y))

    def _on_click(self, event):
        if not self._enabled:
            return
        key = self._hit(event.x, event.y)
        if key:
            self.on_step(self.name, key == "plus")

    def set_value(self, value, changed=True):
        self.value = value
        self._changed = self._changed or changed
        self._draw()

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self._draw()


class SlimScroll(tk.Canvas):
    """A scrollbar that matches the window and gets out of the way.

    Tk uses the platform scrollbar, which on Windows is a pale strip that looks
    pasted on over a dark window. This is the same contract - it is handed to a
    canvas as its yscrollcommand - drawn to match, and it hides itself whenever
    everything already fits.
    """

    WIDTH = 10

    def __init__(self, parent, command, bg=BG):
        tk.Canvas.__init__(self, parent, width=self.WIDTH, bg=bg,
                           highlightthickness=0, bd=0)
        self.command = command
        self.first, self.last = 0.0, 1.0
        self._drag_from = None
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag_from", None))

    def set(self, first, last):
        self.first, self.last = float(first), float(last)
        if self.first <= 0.0 and self.last >= 1.0:
            self.pack_forget()
        elif not self.winfo_ismapped():
            self.pack(side="right", fill="y", padx=(2, 0))
        self._draw()

    def _geom(self):
        h = self.winfo_height() or 1
        top = int(self.first * h)
        bottom = max(top + 28, int(self.last * h))
        return top, min(bottom, h)

    def _draw(self):
        self.delete("all")
        if self.first <= 0.0 and self.last >= 1.0:
            return
        top, bottom = self._geom()
        round_rect(self, 2, 0, self.WIDTH - 2, self.winfo_height(), 4,
                   fill=mix(BG, "#ffffff", 0.04), outline="")
        round_rect(self, 2, top, self.WIDTH - 2, bottom, 4,
                   fill=mix(CARD, "#ffffff", 0.18), outline="")

    def _press(self, event):
        top, bottom = self._geom()
        if top <= event.y <= bottom:
            self._drag_from = event.y - top
        else:
            self._jump(event.y - (bottom - top) / 2.0)

    def _drag(self, event):
        if self._drag_from is not None:
            self._jump(event.y - self._drag_from)

    def _jump(self, y):
        h = float(self.winfo_height() or 1)
        self.command("moveto", max(0.0, min(1.0, y / h)))


# --------------------------------------------------------------------- worker
class Worker(threading.Thread):
    """Owns the wire. Speaks only in messages."""

    def __init__(self):
        threading.Thread.__init__(self)
        self.daemon = True
        self.jobs = queue.Queue()
        self.out = queue.Queue()
        self.br = None
        self.menu = None

    def say(self, text, tone="info"):
        self.out.put(("status", text, tone))

    def run(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            name, args = job[0], job[1:]
            try:
                getattr(self, "do_" + name)(*args)
            except MenuError as exc:
                self.say(str(exc), "bad")
                self.out.put(("busy", False))
            except Exception as exc:
                self.say("%s: %s" % (type(exc).__name__, exc), "bad")
                self.out.put(("busy", False))

    def do_scan(self):
        self.say("looking at every serial port...")
        found = discover.scan()
        self.out.put(("ports", found))
        best = discover.pick(found)
        if best is None:
            self.say("no flight controller and no adapter answered", "warn")
        else:
            self.say("found %s" % best.label(), "good")
        self.out.put(("busy", False))

    def do_connect(self, device, kind):
        self.close()
        self.say("opening %s..." % device)
        self.br = open_transport("inav" if kind == "inav" else "esp32", device)
        from . import srxl2
        self.br.set_baud(srxl2.BAUD_LOW)
        self.br.reset()
        self.br.report_echo(False)
        self.menu = AvianMenu(self.br, log=lambda m: self.say(m))
        self.say("waiting for the ESC - switch it on now if it is not already",
                 "warn")
        self.menu.connect(timeout=25.0)
        self.out.put(("linked", self.menu.device))
        self.say("ESC 0x%02X answered" % self.menu.device, "good")
        self.do_open()

    def do_open(self):
        self.say("opening the ESC's menu, about twenty seconds")
        self.menu.enter()
        self.do_read()

    def do_read(self):
        self.say("reading the ESC's parameters, one step at a time...")
        self.out.put(("entries_begin",))

        def found(name, value, n):
            if name not in ACTIONS:
                self.out.put(("entry", name, value))
            self.say("read %d so far: %s is %s" % (n, name.title(), value or "-"))

        entries = self.menu.walk(on_found=found)
        kept = [e for e in entries if e[0] not in ACTIONS]
        self.out.put(("entries_end", len(kept)))
        self.say("%d parameters, all read from the ESC" % len(kept), "good")
        self.out.put(("busy", False))

    def do_bump(self, name, forward):
        value = self.menu.bump(name, forward)
        self.out.put(("value", name, value))
        self.say("%s is now %s" % (name.title(), value))
        self.out.put(("busy", False))

    def do_action(self, name):
        self.menu.activate(name)
        if name == "EXIT W/ SAVE":
            self.say("written to the ESC and kept", "good")
        elif name == "DEFAULT/EXIT":
            self.say("factory defaults restored", "good")
        else:
            self.say("left the menu without writing anything", "warn")
        self.close()
        self.out.put(("closed",))
        self.out.put(("busy", False))

    def close(self):
        if self.br is not None:
            try:
                self.br.close()
            except Exception:
                pass
        self.br = None
        self.menu = None


# ------------------------------------------------------------------ interface
class App(object):
    def __init__(self, root):
        self.root = root
        self.worker = Worker()
        self.worker.start()
        self.ports = []
        self.rows = {}
        self.busy = False
        self.logo = None

        root.title("Smart ESC Tool")
        root.configure(bg=BG)
        root.geometry("820x640")
        root.minsize(720, 520)
        self._set_icon()

        self.fonts = {
            "title": tkfont.Font(family="Segoe UI Semibold", size=16),
            "sub": tkfont.Font(family="Segoe UI", size=9),
            "body": tkfont.Font(family="Segoe UI", size=11),
            "small": tkfont.Font(family="Segoe UI", size=9),
            "value": tkfont.Font(family="Consolas", size=11),
            "glyph": tkfont.Font(family="Segoe UI", size=15),
        }

        self._build_header()
        self._build_list()
        self._build_footer()

        self.set_busy(True)
        self.worker.jobs.put(("scan",))
        self.root.after(60, self._drain)

    # -- chrome ----------------------------------------------------------
    def _set_icon(self):
        path = resource("icon.ico")
        if path:
            try:
                self.root.iconbitmap(default=path)
            except Exception:
                pass
        png = resource("icon.png")
        if png:
            try:
                self.logo = tk.PhotoImage(file=png).subsample(6, 6)
                self.root.iconphoto(True, tk.PhotoImage(file=png).subsample(4, 4))
            except Exception:
                self.logo = None

    def _build_header(self):
        self.head = tk.Canvas(self.root, height=96, bg=HEAD_BOTTOM,
                              highlightthickness=0, bd=0)
        self.head.pack(fill="x")
        self.head.bind("<Configure>", lambda e: self._paint_header())

        self.port_var = tk.StringVar(value="looking for boards...")
        self.port_menu = tk.OptionMenu(self.head, self.port_var, "looking...")
        self.port_menu.configure(
            bg=CARD, fg=TEXT, activebackground=EDGE, activeforeground=TEXT,
            relief="flat", bd=0, highlightthickness=1, highlightbackground=EDGE,
            width=28, font=self.fonts["small"], anchor="w", cursor="hand2",
            padx=10, pady=6)
        self.port_menu["menu"].configure(bg=CARD, fg=TEXT, activebackground=ACCENT,
                                         activeforeground="#0b1220", bd=0,
                                         relief="flat", font=self.fonts["small"])
        self.btn_connect = RoundButton(self.head, "Connect", self.on_connect,
                                       kind="primary", font=self.fonts["small"],
                                       bg=HEAD_BOTTOM, width=110)
        self._paint_header()

    def _paint_header(self):
        c = self.head
        w = c.winfo_width() or 820
        h = 96
        c.delete("paint")
        for y in range(h):
            c.create_line(0, y, w, y, tags="paint",
                          fill=mix(HEAD_TOP, HEAD_BOTTOM, y / float(h)))
        c.create_line(0, h - 1, w, h - 1, fill=EDGE, tags="paint")

        x = 26
        if self.logo is not None:
            c.create_image(x, h / 2, image=self.logo, anchor="w", tags="paint")
            x += self.logo.width() + 16
        c.create_text(x, h / 2 - 11, text="Smart ESC Tool", anchor="w",
                      fill=TEXT, font=self.fonts["title"], tags="paint")
        c.create_text(x, h / 2 + 13, text=self.subtitle_text(), anchor="w",
                      fill=DIM, font=self.fonts["sub"], tags="paint")

        c.create_window(w - 26, h / 2, window=self.btn_connect, anchor="e",
                        tags="paint")
        c.create_window(w - 150, h / 2, window=self.port_menu, anchor="e",
                        tags="paint")

    def subtitle_text(self):
        return getattr(self, "_subtitle", "Spektrum Avian  ·  SRXL2")

    def set_subtitle(self, text):
        self._subtitle = text
        self._paint_header()

    def _build_list(self):
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill="both", expand=True, padx=14, pady=(12, 4))

        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        self.bar = SlimScroll(wrap, self.canvas.yview)
        self.body = tk.Frame(self.canvas, bg=BG)
        self.body.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfig(self.window, width=e.width))
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        self.show_placeholder(
            "Plug in a flight controller running INAV, or the SRXL2 adapter.\n"
            "Press Connect, then switch the ESC on when the window asks.")

    def _build_footer(self):
        foot = tk.Frame(self.root, bg=BG, height=76)
        foot.pack(fill="x", side="bottom")
        foot.pack_propagate(False)

        line = tk.Frame(foot, bg=EDGE, height=1)
        line.pack(fill="x", side="top")

        self.dot = tk.Canvas(foot, width=12, height=12, bg=BG,
                             highlightthickness=0, bd=0)
        self.dot.pack(side="left", padx=(22, 8))
        self.status = tk.Label(foot, text="", bg=BG, fg=DIM,
                               font=self.fonts["small"], anchor="w")
        self.status.pack(side="left")

        self.btn_exit = RoundButton(foot, "Discard", lambda: self.on_action("EXIT"),
                                    font=self.fonts["small"], width=92)
        self.btn_exit.pack(side="right", padx=(6, 22), pady=18)
        self.btn_default = RoundButton(foot, "Restore defaults",
                                       lambda: self.on_action("DEFAULT/EXIT"),
                                       font=self.fonts["small"], width=134)
        self.btn_default.pack(side="right", padx=6, pady=18)
        self.btn_save = RoundButton(foot, "Save to ESC",
                                    lambda: self.on_action("EXIT W/ SAVE"),
                                    kind="good", font=self.fonts["small"], width=124)
        self.btn_save.pack(side="right", padx=6, pady=18)
        self.say("starting")

    def _wheel(self, event):
        self.canvas.yview_scroll(-1 * (event.delta // 120), "units")

    # -- state -----------------------------------------------------------
    def set_busy(self, busy):
        self.busy = busy
        for b in (self.btn_connect, self.btn_save, self.btn_default, self.btn_exit):
            b.configure_state(not busy)
        for row in self.rows.values():
            row.set_enabled(not busy)

    def say(self, text, tone="info"):
        colour = {"good": GOOD, "warn": WARN, "bad": BAD}.get(tone, DIM)
        self.status.configure(text=text, fg=colour)
        self.dot.delete("all")
        self.dot.create_oval(2, 2, 10, 10, fill=colour, outline="")

    # -- events ----------------------------------------------------------
    def on_connect(self):
        label = self.port_var.get()
        for f in self.ports:
            if f.label() == label:
                if not f.usable:
                    self.say("nothing on %s answered a question only the right "
                             "board can" % f.device, "warn")
                    return
                self.set_busy(True)
                self.say("connecting to %s" % f.device)
                self.worker.jobs.put(("connect", f.device, f.kind))
                return
        self.set_busy(True)
        self.worker.jobs.put(("scan",))

    def on_bump(self, name, forward):
        if self.busy:
            return
        self.set_busy(True)
        self.say("%s: asking the ESC for the %s value"
                 % (name.title(), "next" if forward else "previous"))
        self.worker.jobs.put(("bump", name, forward))

    def on_action(self, name):
        if self.busy:
            return
        self.set_busy(True)
        self.worker.jobs.put(("action", name))

    # -- drawing ---------------------------------------------------------
    def show_placeholder(self, text):
        for child in self.body.winfo_children():
            child.destroy()
        self.rows = {}
        holder = tk.Frame(self.body, bg=BG)
        holder.pack(fill="both", expand=True, pady=70)
        tk.Label(holder, text=text, bg=BG, fg=DIM, font=self.fonts["body"],
                 justify="center").pack()

    def show_ports(self, found):
        self.ports = found
        menu = self.port_menu["menu"]
        menu.delete(0, "end")
        for f in found:
            menu.add_command(label=f.label(),
                             command=lambda v=f.label(): self.port_var.set(v))
        usable = [f for f in found if f.usable]
        if usable:
            self.port_var.set(usable[0].label())
        elif found:
            self.port_var.set(found[0].label())
        else:
            self.port_var.set("no serial ports found")

    def begin_entries(self):
        for child in self.body.winfo_children():
            child.destroy()
        self.rows = {}
        self.note = None

    def add_entry(self, name, value):
        """One parameter, as soon as the ESC has given it up."""
        if name in self.rows:
            self.rows[name].set_value(value, changed=False)
            return
        row = ParamRow(self.body, name, value, self.on_bump, self.fonts)
        row.set_enabled(not self.busy)
        row.pack(fill="x", pady=1)
        self.rows[name] = row
        self.canvas.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def end_entries(self, count):
        if getattr(self, "note", None) is None:
            self.note = tk.Label(
                self.body, bg=BG, fg=WARN, font=self.fonts["small"],
                justify="left", wraplength=680,
                text="A change takes effect at once, and is gone at the next "
                     "power-up unless you press Save to ESC.")
            self.note.pack(anchor="w", padx=22, pady=(14, 18))

    def show_entries(self, entries):
        self.begin_entries()
        for name, value in entries:
            if name not in ACTIONS:
                self.add_entry(name, value)
        self.end_entries(len(self.rows))

    def _drain(self):
        try:
            while True:
                msg = self.worker.out.get_nowait()
                kind = msg[0]
                if kind == "status":
                    self.say(msg[1], msg[2])
                elif kind == "ports":
                    self.show_ports(msg[1])
                elif kind == "entries_begin":
                    self.begin_entries()
                elif kind == "entry":
                    self.add_entry(msg[1], msg[2])
                elif kind == "entries_end":
                    self.end_entries(msg[1])
                    self.set_busy(False)
                elif kind == "entries":
                    self.show_entries(msg[1])
                    self.set_busy(False)
                elif kind == "value":
                    row = self.rows.get(msg[1])
                    if row:
                        row.set_value(msg[2])
                elif kind == "linked":
                    self.set_subtitle("ESC 0x%02X  ·  menu open" % msg[1])
                elif kind == "closed":
                    self.set_subtitle("Spektrum Avian  ·  SRXL2")
                    self.show_placeholder(
                        "The ESC has left its menu.\n"
                        "Switch it off and on, then press Connect to go back in.")
                elif kind == "busy":
                    self.set_busy(msg[1])
        except queue.Empty:
            pass
        self.root.after(60, self._drain)


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            # Without this Windows groups the window under the interpreter and
            # shows its icon in the taskbar instead of ours.
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                u"smart-esc-tool.avian")
        except Exception:
            pass
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.25)
    except Exception:
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    main()
