// The menu driver against the simulated Avian, on a virtual clock: a whole session takes milliseconds
import { test } from "node:test";
import assert from "node:assert/strict";
import { AvianMenu, LinkLost } from "../src/avian_menu.js";
import { FakeAvian } from "../src/fake_avian.js";

function session(options = {}) {
  let t = 0;
  const clock = () => t;
  const wait = async (ms) => {
    t += ms;
  };
  const esc = new FakeAvian({ clock, ...options });
  const log = [];
  const menu = new AvianMenu(esc, { clock, wait, log: (m) => log.push(m) });
  return { esc, menu, log, time: () => t, stall: (ms) => wait(ms) };
}

test("finds an ESC that announces itself", async () => {
  const { menu } = session();
  assert.equal(await menu.connect(), 0x40);
});

test("finds an ESC that only answers when called", async () => {
  const { menu } = session({ id: 0x43, announces: false });
  assert.equal(await menu.connect(), 0x43);
});

test("gives up on a silent wire, saying why", async () => {
  const { menu } = session({ announces: false, id: 0x50 }); // outside 0x40..0x4F: never called
  await assert.rejects(menu.connect(2000), /no ESC answered/);
});

for (const echo of [false, true]) {
  test(`a whole session${echo ? ", with our own bytes echoed back as on a flight controller" : ""}`, async () => {
    const { esc, menu, time } = session({ echo });
    await menu.enter();
    assert.ok(menu.isOpen(), "menu open after the two-step hold");
    assert.ok(time() > 16000, "the hold took the ESC's own time");

    const seen = [];
    const entries = await menu.walk(40, (name, value, n) => seen.push([name, value, n]));
    // the list in docs/text-menu.md: fourteen parameters, then the three actions
    assert.deepEqual(
      entries.map((e) => e.name),
      [
        "FLIGHT MODE", "BRAKE TYPE", "BRAKE FORCE", "CUTOFF TYPE", "LIPO CELLS", "CUTOFF VOLT", "BEC VOLTAGE",
        "STARTUP TIME", "MOTOR ROTATE", "ACTIVE FW", "GOV GAIN", "AR TIME", "RESTARTACCEL", "THRUST REV",
        "EXIT W/ SAVE", "DEFAULT/EXIT", "EXIT",
      ],
    );
    assert.equal(seen.length, entries.length);
    assert.equal(entries[1].value, "Disabled");
    assert.equal(entries[13].value, "CH7");

    // reverse, as the doc describes it: BRAKE TYPE's fourth value
    assert.equal(await menu.bump("BRAKE TYPE", false), "Reverse", "previous value wraps to the last");
    assert.equal(await menu.bump("BRAKE FORCE", true), "1");
    assert.deepEqual(esc.current.slice(1, 3), [3, 1]);
    assert.deepEqual(esc.saved.slice(1, 3), [0, 0], "nothing kept before saving");

    assert.equal(await menu.activate("EXIT W/ SAVE"), true, "the ESC is picked up again after it restarts");
    assert.deepEqual(esc.saved.slice(1, 3), [3, 1]);
    assert.ok(esc.linked);
    assert.equal(esc.drops, 0, "the feed never left a gap the ESC would drop the link for");
  });
}

test("the simulated ESC lets its master go after a quarter of a second without a frame", async () => {
  const { esc, menu, stall } = session();
  await menu.connect();
  await menu.pump(100);
  assert.ok(esc.linked);
  await stall(300);
  esc.poll();
  assert.equal(esc.drops, 1);
});

test("a value the ESC never reported is not passed off as its answer", async () => {
  const { esc, menu } = session();
  await menu.enter();
  esc.reply = () => {}; // gone quiet: the screen still holds its last rows
  await assert.rejects(menu.bump("FLIGHT MODE", true), LinkLost);
});

test("leaving without saving keeps what was saved", async () => {
  const { esc, menu } = session();
  await menu.enter();
  const before = [...esc.saved];
  await menu.bump("CUTOFF VOLT", true);
  assert.equal(await menu.activate("EXIT"), true);
  assert.deepEqual(esc.saved, before);
  assert.deepEqual(esc.current, before);
});

test("asking for a parameter the ESC does not have names the ones it has", async () => {
  const { menu } = session();
  await menu.enter();
  await assert.rejects(menu.bump("GOVERNOR", true), /no parameter called "GOVERNOR"/);
});
