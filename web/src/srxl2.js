// SRXL2 framing, ported from smart_esc_tool/srxl2.py (itself mirrored from INAV's motor_srxl2.c). Only what the
// application uses: handshake, control data, frame splitting and ESC telemetry.

export const MAGIC = 0xa6;

export const HANDSHAKE = 0x21;
export const TELEMETRY = 0x80;
export const CONTROL = 0xcd;

export const CMD_CHANNEL_DATA = 0x00;

export const OUR_DEVICE_ID = 0x31; // flight controller type, unit 1, as INAV
export const ESC_ID_FIRST = 0x40;
export const ESC_ID_LAST = 0x4f;
export const BROADCAST = 0xff;

export const BAUD_LOW = 115200;
export const BAUD_BIT_400K = 0x01;

export const TELEM_SENSOR_ESC = 0x20;
export const TELEM_SENSOR_TEXTGEN = 0x0c;

// CRC-16/CCITT, poly 0x1021, init 0, no reflection
export function crc16(data, crc = 0) {
  for (const b of data) {
    crc ^= b << 8;
    for (let i = 0; i < 8; i++) {
      crc = crc & 0x8000 ? ((crc << 1) ^ 0x1021) & 0xffff : (crc << 1) & 0xffff;
    }
  }
  return crc;
}

// Patch the length byte and append the CRC, big-endian on the wire
export function seal(bytes) {
  const out = new Uint8Array(bytes.length + 2);
  out.set(bytes);
  out[2] = out.length;
  const c = crc16(out.subarray(0, out.length - 2));
  out[out.length - 2] = c >> 8;
  out[out.length - 1] = c & 0xff;
  return out;
}

// a trailing CRC makes the CRC of the whole frame zero
export function crcOk(frame) {
  return frame.length >= 5 && crc16(frame) === 0;
}

// us = 988 + (value >> 6): 1500 us lands on 0x8000, "Servo Center"
export function usToValue(us) {
  const v = (Math.trunc(us) - 988) << 6;
  return Math.max(0, Math.min(0xfffc, v)) & 0xfffc;
}

export function valueToUs(value) {
  return 988 + (value >> 6);
}

function le32(value) {
  return [value & 0xff, (value >>> 8) & 0xff, (value >>> 16) & 0xff, (value >>> 24) & 0xff];
}

export function handshake(dest, baudBits = 0, source = OUR_DEVICE_ID, priority = 10, uid = 0x494e4156) {
  return seal(Uint8Array.from([MAGIC, HANDSHAKE, 0, source, dest, priority, baudBits, 0, ...le32(uid)]));
}

// channels: Map or object {index0based: value}. Channels not given are not sent at all rather than centred: on an
// aircraft ESC 1500 us is half throttle, so a wrong guess about the throttle index should leave the motor idle.
export function controlData(channels, replyId = 0x00, rssi = 100, losses = 0, command = CMD_CHANNEL_DATA) {
  const indexes = Object.keys(channels)
    .map(Number)
    .sort((a, b) => a - b);
  let mask = 0;
  for (const i of indexes) {
    mask |= 1 << i;
  }
  const body = [MAGIC, CONTROL, 0, command, replyId, rssi, losses & 0xff, (losses >> 8) & 0xff, ...le32(mask)];
  for (const i of indexes) {
    const v = Math.trunc(channels[i]);
    body.push(v & 0xff, (v >> 8) & 0xff);
  }
  return seal(Uint8Array.from(body));
}

// Whole frames out of a byte stream: {frames, rest}. Resyncs on the magic byte, which a single wire needs: the first
// bytes after a turnaround are routinely the tail of something we were not listening for.
export function split(stream) {
  const frames = [];
  let i = 0;
  for (;;) {
    while (i < stream.length && stream[i] !== MAGIC) {
      i++;
    }
    if (i + 3 > stream.length) {
      return { frames, rest: stream.slice(i) };
    }
    const length = stream[i + 2];
    if (length < 5 || length > 80) {
      i++; // not a plausible header, keep scanning
      continue;
    }
    if (i + length > stream.length) {
      return { frames, rest: stream.slice(i) };
    }
    frames.push(stream.slice(i, i + length));
    i += length;
  }
}

// STRU_TELE_ESC, big-endian; 0xFFFF and 0xFF mean "no data"
export function decodeEscTelemetry(payload) {
  const be16 = (o) => (payload[o] << 8) | payload[o + 1];
  const rpm = be16(2);
  const volts = be16(4);
  const tfet = be16(6);
  const curr = be16(8);
  const tbec = be16(10);
  return {
    sensorId: payload[0],
    rpm: rpm === 0xffff ? null : rpm * 10,
    voltageV: volts === 0xffff ? null : volts / 100,
    tempFetC: tfet === 0xffff ? null : tfet / 10,
    currentA: curr === 0xffff ? null : curr / 100,
    tempBecC: tbec === 0xffff ? null : tbec / 10,
    currentBec: payload[12] === 0xff ? null : payload[12] / 10,
    voltsBec: payload[13] === 0xff ? null : payload[13] / 20,
    throttlePc: payload[14] === 0xff ? null : payload[14] / 2,
    powerPc: payload[15] === 0xff ? null : payload[15] / 2,
  };
}
