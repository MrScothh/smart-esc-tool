"""A window for configuring a Spektrum Avian, through a flight controller.

The ESC keeps its own menu and will not hold a link for longer than a quarter
of a second without a frame, so the wire has to be fed continuously while a
person reads the screen and decides what to change. That rules out doing any of
it on the interface thread: a worker owns the transport and the menu, takes one
instruction at a time from a queue, and posts back what the ESC now says. The
window only ever draws what came back, and every value it shows was read from
the ESC after the change rather than assumed from what was asked for.

The interface is PySide6 with Fluent components, so it inherits Windows 11's
conventions - accent colour, light and dark following the system, the usual
metrics - instead of hand-drawn controls that have to imitate them.
"""

import os
import queue
import sys
import threading

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QSizePolicy,
                               QVBoxLayout, QWidget)

from qfluentwidgets import (BodyLabel, CaptionLabel, ComboBox, FluentIcon,
                            IconWidget, SubtitleLabel, IndeterminateProgressBar, InfoBar,
                            InfoBarPosition, PrimaryPushButton, PushButton,
                            SettingCard, SettingCardGroup, SmoothScrollArea,
                            StrongBodyLabel, Theme, TransparentToolButton,
                            isDarkTheme, setTheme)

from . import discover
from .avian_menu import AvianMenu, MenuError
from .transport import open_transport
from .transport.inav import esc_linked

ACTIONS = ("EXIT W/ SAVE", "DEFAULT/EXIT", "EXIT")

# One spacing scale, used everywhere, so nothing is spaced by eye.
GAP_S, GAP_M, GAP_L = 8, 16, 24


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
        self.kind = None
        self.device = None

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
        self.say("Looking at every serial port")
        found = discover.scan()
        self.out.put(("ports", found))
        best = discover.pick(found)
        if best is None:
            # Worth saying, because the list above cannot show an absence.
            self.say("No flight controller and no adapter answered", "warn")
        else:
            # What was found is already named in the list; repeating it here
            # says the same thing twice and leaves nothing for the line that
            # is meant to narrate what is happening.
            self.say("")
        self.out.put(("busy", False))

    def do_connect(self, device, kind):
        self.close()
        self.say("Opening %s" % device)
        self.kind, self.device = kind, device
        self.br = open_transport("inav" if kind == "inav" else "esp32", device)
        from . import srxl2
        self.br.set_baud(srxl2.BAUD_LOW)
        self.br.reset()
        self.br.report_echo(False)
        self.menu = AvianMenu(self.br, log=lambda m: self.say(m))
        self.say("Waiting for the ESC. Switch it on now if it is not already",
                 "warn")
        self.menu.connect()
        self.out.put(("linked", self.menu.device))
        self.say("ESC 0x%02X answered" % self.menu.device, "good")
        self.do_open()

    def do_open(self):
        self.say("Opening the ESC's menu, about twenty seconds")
        self.menu.enter()
        self.do_read()

    def do_read(self):
        self.say("Reading the ESC's parameters")
        self.out.put(("entries_begin",))

        def found(name, value, n):
            if name not in ACTIONS:
                self.out.put(("entry", name, value))
            self.say("Read %d so far. %s is %s" % (n, name.title(), value or "-"))

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
            self.say("Saved. The ESC will keep these settings", "good")
        elif name == "DEFAULT/EXIT":
            self.say("Factory defaults restored", "good")
        else:
            self.say("Left the menu without writing anything", "warn")
        self.close()
        linked = None
        if self.kind == "inav":
            # Leaving restarts the ESC; whether the flight controller caught it
            # again is something to ask, not to assume
            self.say("Asking the flight controller whether it has the ESC again")
            linked = esc_linked(self.device)
        self.out.put(("closed", name, linked))
        self.out.put(("busy", False))

    def close(self):
        if self.br is not None:
            try:
                self.br.close()
            except Exception:
                pass
        self.br = None
        self.menu = None


# ---------------------------------------------------------------------- cards
class ParameterCard(SettingCard):
    """One parameter: what it is, what it is set to, and a step either way.

    The value sits between the two steps, so the thing that changes is between
    the controls that change it.
    """

    def __init__(self, name, value, on_step, parent=None):
        SettingCard.__init__(self, FluentIcon.SETTING, name.title(), None, parent)
        self.name = name
        self.on_step = on_step
        self.initial = value
        self.modified = False

        # The same gear fifteen times says nothing about any of the rows; the
        # name already identifies the parameter, and dropping the icon leaves
        # the value as the only thing on the row that changes.
        self.iconLabel.setFixedSize(0, 0)
        self.iconLabel.hide()

        self.previous = TransparentToolButton(FluentIcon.LEFT_ARROW, self)
        self.previous.setToolTip("Previous value")
        self.previous.clicked.connect(lambda: self.on_step(self.name, False))

        self.value = StrongBodyLabel(value or "-", self)
        self.value.setAlignment(Qt.AlignCenter)
        self.value.setMinimumWidth(136)

        self.next = TransparentToolButton(FluentIcon.RIGHT_ARROW, self)
        self.next.setToolTip("Next value")
        self.next.clicked.connect(lambda: self.on_step(self.name, True))

        self.hBoxLayout.addWidget(self.previous, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(GAP_S)
        self.hBoxLayout.addWidget(self.value, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(GAP_S)
        self.hBoxLayout.addWidget(self.next, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(GAP_M)

    def set_value(self, value):
        self.value.setText(value or "-")
        self.modified = (value or "") != (self.initial or "")
        if self.modified:
            self.value.setTextColor("#0067c0", "#60cdff")
        else:
            self.value.setTextColor("#1a1a1a", "#ffffff")

    def set_enabled(self, enabled):
        self.previous.setEnabled(enabled)
        self.next.setEnabled(enabled)


# --------------------------------------------------------------------- window
class Window(QWidget):
    def __init__(self):
        QWidget.__init__(self)
        self.worker = Worker()
        self.worker.start()
        self.ports = []
        self.cards = {}
        self.busy = False
        # Save, defaults and leave act on an open menu; without one they have
        # nothing to act on
        self.menu_open = False

        self.setWindowTitle("Smart ESC Tool")
        self.resize(880, 700)
        self.setMinimumSize(QSize(720, 520))
        icon = resource("icon.ico") or resource("icon.png")
        if icon:
            self.setWindowIcon(QIcon(icon))

        root = QVBoxLayout(self)
        root.setContentsMargins(GAP_L, GAP_L, GAP_L, GAP_M)
        root.setSpacing(GAP_M)
        root.addLayout(self._header())
        root.addWidget(self._progress())
        root.addWidget(self._body(), 1)
        root.addWidget(self._separator())
        root.addLayout(self._footer())

        self.set_busy(True)
        self.worker.jobs.put(("scan",))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._drain)
        self.timer.start(60)

    # -- chrome ----------------------------------------------------------
    def _header(self):
        row = QHBoxLayout()
        row.setSpacing(GAP_M)

        mark = resource("icon.png")
        if mark:
            badge = IconWidget(QIcon(mark), self)
            badge.setFixedSize(40, 40)
            row.addWidget(badge, 0, Qt.AlignVCenter)

        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(SubtitleLabel("Smart ESC Tool", self))
        self.subtitle = CaptionLabel("Spektrum Avian over SRXL2", self)
        self.subtitle.setTextColor("#5d5d5d", "#9f9f9f")
        words.addWidget(self.subtitle)
        row.addLayout(words)
        row.addStretch(1)

        self.ports_box = ComboBox(self)
        self.ports_box.setMinimumWidth(280)
        self.ports_box.setPlaceholderText("Looking for boards")
        row.addWidget(self.ports_box, 0, Qt.AlignVCenter)

        self.connect_button = PrimaryPushButton(FluentIcon.CONNECT, "Connect", self)
        self.connect_button.clicked.connect(self.on_connect)
        row.addWidget(self.connect_button, 0, Qt.AlignVCenter)
        return row

    def _progress(self):
        self.progress = IndeterminateProgressBar(self)
        self.progress.setFixedHeight(3)
        return self.progress

    def _body(self):
        self.scroll = SmoothScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("background: transparent;")

        self.page = QWidget(self.scroll)
        self.page.setStyleSheet("background: transparent;")
        self.page_layout = QVBoxLayout(self.page)
        self.page_layout.setContentsMargins(0, 0, GAP_S, GAP_M)
        self.page_layout.setSpacing(GAP_M)

        self.group = SettingCardGroup("Parameters", self.page)
        self.page_layout.addWidget(self.group)

        self.empty = BodyLabel(
            "Plug in a flight controller running INAV, or the SRXL2 adapter,\n"
            "then choose it above and press Connect.", self.page)
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignCenter)
        # A measure that reads as a paragraph: at full window width a message
        # of two sentences leaves a single word on its second line. Fixed,
        # because a centred label otherwise shrinks to Qt's guess at a width,
        # and 560 still fits the narrowest the window may be
        self.empty.setFixedWidth(560)
        self.page_layout.addWidget(self.empty, 0, Qt.AlignHCenter)
        self.page_layout.addStretch(1)

        # Two stretches would fight each other, so only one is ever active:
        # the message takes the whole height and centres in it, and the list
        # sits at the top with the slack below it.
        self._empty_slot = self.page_layout.count() - 2
        self._tail_slot = self.page_layout.count() - 1
        self._balance(empty=True)

        self.group.setVisible(False)
        self.scroll.setWidget(self.page)
        return self.scroll

    def _separator(self):
        """Where the list stops and the actions begin.

        Without it the last row runs under the buttons and the two read as one
        surface, which matters here because those buttons end the session.
        """
        line = QFrame(self)
        line.setFrameShape(QFrame.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet(
            "background-color: rgba(255,255,255,18); border: none;"
            if isDarkTheme() else
            "background-color: rgba(0,0,0,18); border: none;")
        return line

    def _balance(self, empty):
        self.page_layout.setStretch(self._empty_slot, 1 if empty else 0)
        self.page_layout.setStretch(self._tail_slot, 0 if empty else 1)

    def _footer(self):
        row = QHBoxLayout()
        row.setSpacing(GAP_S)

        self.status = CaptionLabel("", self)
        self.status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row.addWidget(self.status, 1, Qt.AlignVCenter)

        # The ESC applies a change at once and forgets it at the next power-up
        # unless it is saved, so how many are still unwritten is worth a line
        # of its own rather than a sentence someone has to remember.
        self.unsaved = CaptionLabel("", self)
        self.unsaved.setTextColor("#0067c0", "#60cdff")
        row.addWidget(self.unsaved, 0, Qt.AlignVCenter)
        row.addSpacing(GAP_S)

        self.save_button = PrimaryPushButton(FluentIcon.SAVE, "Save to ESC", self)
        self.save_button.clicked.connect(lambda: self.on_action("EXIT W/ SAVE"))
        self.defaults_button = PushButton(FluentIcon.HISTORY, "Restore defaults", self)
        self.defaults_button.clicked.connect(lambda: self.on_action("DEFAULT/EXIT"))
        self.leave_button = PushButton("Leave without saving", self)
        self.leave_button.clicked.connect(lambda: self.on_action("EXIT"))
        for b in (self.save_button, self.defaults_button, self.leave_button):
            row.addWidget(b, 0, Qt.AlignVCenter)
        return row

    # -- state -----------------------------------------------------------
    def set_busy(self, busy):
        self.busy = busy
        self.progress.setVisible(busy)
        self.connect_button.setEnabled(not busy)
        for b in (self.save_button, self.defaults_button, self.leave_button):
            b.setEnabled(not busy and self.menu_open)
        for card in self.cards.values():
            card.set_enabled(not busy)

    def say(self, text, tone="info"):
        self.status.setText(text)
        colours = {"good": ("#0f7b0f", "#6ccb5f"),
                   "warn": ("#9d5d00", "#fce100"),
                   "bad": ("#c42b1c", "#ff99a4")}
        light, dark = colours.get(tone, ("#5d5d5d", "#9f9f9f"))
        self.status.setTextColor(light, dark)

    def notify(self, title, text, tone):
        maker = {"good": InfoBar.success, "warn": InfoBar.warning,
                 "bad": InfoBar.error}.get(tone, InfoBar.info)
        maker(title=title, content=text, orient=Qt.Horizontal, isClosable=True,
              position=InfoBarPosition.TOP_RIGHT, duration=5000, parent=self)

    # -- events ----------------------------------------------------------
    def on_connect(self):
        index = self.ports_box.currentIndex()
        if index < 0 or index >= len(self.ports):
            self.set_busy(True)
            self.worker.jobs.put(("scan",))
            return
        found = self.ports[index]
        if not found.usable:
            self.notify("Nothing answered",
                        "%s did not answer a question only the right board can."
                        % found.device, "warn")
            return
        self.menu_open = False
        self.set_busy(True)
        self.say("Connecting to %s" % found.device)
        self.worker.jobs.put(("connect", found.device, found.kind))

    def on_step(self, name, forward):
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
    def show_ports(self, found):
        self.ports = found
        self.ports_box.clear()
        for f in found:
            self.ports_box.addItem(f.label())
        for i, f in enumerate(found):
            if f.usable:
                self.ports_box.setCurrentIndex(i)
                break
        if not found:
            self.ports_box.setPlaceholderText("No serial ports found")

    def _clear_cards(self):
        self.unsaved.setText("")
        for card in self.cards.values():
            card.setParent(None)
            card.deleteLater()
        self.cards = {}

    def begin_entries(self):
        self._clear_cards()
        self.empty.setVisible(False)
        self.group.setVisible(True)
        self.group.adjustSize()
        self._balance(empty=False)

    def add_entry(self, name, value):
        if name in self.cards:
            self.cards[name].set_value(value)
            return
        card = ParameterCard(name, value, self.on_step, self.group)
        card.set_enabled(not self.busy)
        self.group.addSettingCard(card)
        # A child created after its parent is already on screen is not shown by
        # Qt on its own, and the group's layout skips anything hidden - so the
        # cards existed, were positioned, and were invisible.
        card.show()
        self.cards[name] = card
        self.group.adjustSize()

    def count_unsaved(self):
        n = sum(1 for c in self.cards.values() if c.modified)
        self.unsaved.setText("" if not n else
                             "1 change not saved" if n == 1 else
                             "%d changes not saved" % n)

    def end_entries(self, count):
        self.group.adjustSize()
        self.count_unsaved()
        self.notify("Read from the ESC",
                    "%d parameters. A change is kept only when you press "
                    "Save to ESC." % count, "good")

    def show_disconnected(self, text):
        self._clear_cards()
        self.group.setVisible(False)
        self.empty.setText(text)
        self.empty.setVisible(True)
        self._balance(empty=True)

    def _drain(self):
        try:
            while True:
                msg = self.worker.out.get_nowait()
                kind = msg[0]
                if kind == "status":
                    self.say(msg[1], msg[2])
                    if msg[2] == "bad":
                        self.notify("Stopped", msg[1], "bad")
                elif kind == "ports":
                    self.show_ports(msg[1])
                elif kind == "entries_begin":
                    self.begin_entries()
                elif kind == "entry":
                    self.add_entry(msg[1], msg[2])
                elif kind == "entries_end":
                    self.end_entries(msg[1])
                    self.menu_open = True
                    self.set_busy(False)
                elif kind == "value":
                    if msg[1] in self.cards:
                        self.cards[msg[1]].set_value(msg[2])
                        self.count_unsaved()
                elif kind == "linked":
                    self.subtitle.setText("ESC 0x%02X, menu open" % msg[1])
                elif kind == "closed":
                    self.menu_open = False
                    self.subtitle.setText("Spektrum Avian over SRXL2")
                    if msg[2] is True:
                        self.say("The flight controller has the ESC again", "good")
                        self.show_disconnected(
                            "The ESC has left its menu and the flight controller "
                            "has it again.\nPress Connect to go back in.")
                    elif msg[2] is False:
                        self.say("The flight controller does not see the ESC", "bad")
                        self.notify("Switch the ESC off and on",
                                    "INAV will not arm until the ESC is back.", "bad")
                        self.show_disconnected(
                            "The ESC has left its menu, but the flight controller "
                            "does not see it.\nSwitch the ESC off and on before "
                            "flying: INAV will not arm until you do.")
                    else:
                        self.show_disconnected(
                            "The ESC has left its menu.\nSwitch it off and on, "
                            "then press Connect to go back in.")
                elif kind == "busy":
                    self.set_busy(msg[1])
        except queue.Empty:
            pass


def _dark_title_bar(widget):
    """Ask Windows for a dark caption when the theme is dark.

    Without it the window wears a light title bar over a dark interface, which
    is the most obvious way an application announces that it is not really a
    Windows application.
    """
    if sys.platform != "win32" or not isDarkTheme():
        return
    try:
        import ctypes
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            int(widget.winId()), 20, ctypes.byref(ctypes.c_int(1)),
            ctypes.sizeof(ctypes.c_int))
    except Exception:
        pass


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                u"smart-esc-tool.avian")
        except Exception:
            pass
        try:
            # The interpreter's own manifest declares per-monitor awareness, so
            # this is invisible when running from source and essential once
            # frozen: without it the packaged application draws at 1x inside a
            # window the system sized for 150%, and two thirds of the window is
            # empty. Must happen before any Qt object exists.
            import ctypes
            # Only has an effect when the manifest has not already settled
            # the question, which is the case when running from source.
            ctypes.windll.user32.SetProcessDpiAwarenessContext.argtypes = [
                ctypes.c_void_p]
            if not ctypes.windll.user32.SetProcessDpiAwarenessContext(
                    ctypes.c_void_p(-4)):
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    setTheme(Theme.AUTO)

    window = Window()
    window.show()
    _dark_title_bar(window)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
