// The JavaScript port against the bytes the Python implementation produces (fixtures.json, from make_fixtures.py)
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as srxl2 from "../src/srxl2.js";
import { HIGH, LOW, MID } from "../src/avian_menu.js";
import { mspRequest, msp2Request } from "../src/msp.js";
import { Esp32Bridge } from "../src/transports.js";

const fixtures = JSON.parse(readFileSync(new URL("./fixtures.json", import.meta.url), "utf8"));
const hex = (bytes) => Buffer.from(bytes).toString("hex");
const bytes = (text) => Uint8Array.from(Buffer.from(text, "hex"));

test("stick values", () => {
  for (const [us, value] of Object.entries(fixtures.us_to_value)) {
    assert.equal(srxl2.usToValue(Number(us)), value, `${us} us`);
  }
});

test("handshakes", () => {
  assert.equal(hex(srxl2.handshake(0x40, srxl2.BAUD_BIT_400K)), fixtures.handshake_40_400k);
  assert.equal(hex(srxl2.handshake(srxl2.BROADCAST, 0)), fixtures.handshake_broadcast);
  assert.ok(srxl2.crcOk(srxl2.handshake(0x40)));
});

test("control data", () => {
  const channels = { 0: LOW, 1: MID, 2: HIGH, 3: MID, 4: MID, 5: MID, 6: MID, 7: MID };
  assert.equal(hex(srxl2.controlData(channels, 0x40)), fixtures.control_menu_40);
  assert.equal(hex(srxl2.controlData({}, 0x41)), fixtures.control_empty);
});

test("splitting a stream resyncs on the magic byte", () => {
  const { frames, rest } = srxl2.split(bytes(fixtures.stream));
  assert.deepEqual(frames.map(hex), fixtures.split_frames);
  assert.equal(hex(rest), fixtures.split_rest);
});

test("ESC telemetry", () => {
  const t = srxl2.decodeEscTelemetry(bytes("200001f40a8c012c006401180a64c850"));
  const p = fixtures.esc_telemetry;
  assert.deepEqual(
    [t.sensorId, t.rpm, t.voltageV, t.tempFetC, t.currentA, t.tempBecC, t.currentBec, t.voltsBec, t.throttlePc, t.powerPc],
    [p.sensor_id, p.rpm, p.voltage_V, p.temp_fet_C, p.current_A, p.temp_bec_C, p.current_bec, p.volts_bec, p.throttle_pc, p.power_pc],
  );
});

test("MSP requests", () => {
  assert.equal(hex(mspRequest(245, [0xfe, 29])), fixtures.msp_passthrough);
  assert.equal(hex(mspRequest(2)), fixtures.msp_fc_variant);
  assert.equal(hex(msp2Request(0x2233)), fixtures.msp2_srxl2_status);
});

test("SLIP framing to the adapter, both ways", async () => {
  const sent = [];
  const link = { write: async (b) => sent.push(b), take: () => link.pending.splice(0), pending: [] };
  const bridge = new Esp32Bridge(link);
  await bridge.write([0xc0, 0x01, 0xdb, 0x02]);
  assert.equal(hex(sent[0]), fixtures.slip);
  // an event back: tag R, stamp 0x01020304, data containing both escaped bytes
  link.pending = [...bytes("c052040302 01a6dbdcdbdd c0".replaceAll(" ", ""))];
  link.take = () => Uint8Array.from(link.pending.splice(0));
  const [event] = bridge.poll();
  assert.equal(event.tag, "R");
  assert.equal(event.us, 0x01020304);
  assert.equal(hex(event.data), "a6c0db");
});
