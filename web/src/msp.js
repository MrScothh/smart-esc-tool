// The few MSP requests the tool sends to a flight controller, and a reader for their replies
export const MSP_FC_VARIANT = 2;
export const MSP_FC_VERSION = 3;
export const MSP_SET_PASSTHROUGH = 245;
export const PASSTHROUGH_SERIAL_FUNCTION_ID = 0xfe;
export const FUNCTION_ESC_SRXL2_ID = 29; // FUNCTION_ESC_SRXL2, its bit index in INAV's io/serial.h
export const MSP2_INAV_ESC_SRXL2_STATUS = 0x2233; // calibration phase, then 1 if INAV has the ESC

const encoder = new TextEncoder();

export function mspRequest(cmd, payload = []) {
  const body = [payload.length, cmd, ...payload];
  const crc = body.reduce((x, b) => x ^ b, 0);
  return Uint8Array.from([...encoder.encode("$M<"), ...body, crc]);
}

function crc8DvbS2(bytes) {
  let crc = 0;
  for (const b of bytes) {
    crc ^= b;
    for (let i = 0; i < 8; i++) {
      crc = crc & 0x80 ? ((crc << 1) ^ 0xd5) & 0xff : (crc << 1) & 0xff;
    }
  }
  return crc;
}

export function msp2Request(cmd, payload = []) {
  const body = [0, cmd & 0xff, cmd >> 8, payload.length & 0xff, payload.length >> 8, ...payload];
  return Uint8Array.from([...encoder.encode("$X<"), ...body, crc8DvbS2(body)]);
}

export function find(buffer, text) {
  const needle = encoder.encode(text);
  outer: for (let i = 0; i + needle.length <= buffer.length; i++) {
    for (let j = 0; j < needle.length; j++) {
      if (buffer[i + j] !== needle[j]) {
        continue outer;
      }
    }
    return i;
  }
  return -1;
}

// payload of the first complete MSP v1 reply in buffer, or null
export function mspReply(buffer) {
  const i = find(buffer, "$M>");
  if (i < 0 || buffer.length < i + 5) {
    return null;
  }
  const length = buffer[i + 3];
  return buffer.length >= i + 5 + length ? buffer.slice(i + 5, i + 5 + length) : null;
}

export function concat(a, b) {
  const out = new Uint8Array(a.length + b.length);
  out.set(a);
  out.set(b, a.length);
  return out;
}
