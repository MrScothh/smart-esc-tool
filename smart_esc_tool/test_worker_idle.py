"""The worker feeds the ESC's link between two instructions and notices when it is gone.

    python -m unittest smart_esc_tool.test_worker_idle
"""
import queue
import threading
import time
import unittest

from .gui import Worker


class FakeMenu(object):
    """Counts the feeding; the link is alive until told otherwise."""

    def __init__(self):
        self.device = 0x40
        self.pumps = 0
        self.alive = True
        self.lock = threading.Lock()

    def pump(self, seconds):
        with self.lock:
            self.pumps += 1
        time.sleep(seconds)

    def answering(self, within=1.5):
        return self.alive


class WorkerIdleTest(unittest.TestCase):
    def setUp(self):
        self.w = Worker()
        self.menu = FakeMenu()
        self.w.menu = self.menu
        self.w.br = object()
        self.w.do_noop = lambda: self.w.out.put(("noop",))
        self.w.start()

    def tearDown(self):
        self.w.jobs.put(None)
        self.w.join(2)

    def drain(self, wait=0.5):
        out = []
        end = time.monotonic() + wait
        while time.monotonic() < end:
            try:
                out.append(self.w.out.get(timeout=0.05))
            except queue.Empty:
                pass
        return out

    def test_feeds_the_link_while_idle(self):
        time.sleep(1.0)
        # one pump per idle tick of about 50 ms of waiting plus 50 ms of feeding
        self.assertGreaterEqual(self.menu.pumps, 6)
        self.assertLessEqual(self.menu.pumps, 25)
        # the instruction queue still works between the ticks
        self.w.jobs.put(("noop",))
        self.assertIn(("noop",), self.drain())

    def test_a_lost_link_is_reported_once(self):
        time.sleep(0.3)
        self.menu.alive = False
        out = self.drain(0.6)
        kinds = [m[0] for m in out]
        self.assertEqual(kinds.count("closed"), 1)
        self.assertEqual(out[kinds.index("status")][2], "bad")
        self.assertIsNone(self.w.menu)
        self.assertIsNone(self.w.br)
        # with the link gone the worker waits for an instruction, it does not spin
        before = self.menu.pumps
        time.sleep(0.3)
        self.assertEqual(self.menu.pumps, before)


if __name__ == "__main__":
    unittest.main()
