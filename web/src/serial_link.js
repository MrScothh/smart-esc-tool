// A Web Serial port seen the way the transports want it: open at a rate, write, take whatever has arrived, reopen at
// another rate (Web Serial cannot change the rate of an open port), close. Reads run in the background into a queue,
// so taking them never blocks the loop that keeps the ESC's link fed.
export class SerialLink {
  constructor(port) {
    this.port = port;
    this.chunks = [];
    this.baudRate = 0;
    this.reader = null;
    this.writer = null;
    this.reading = null;
    this.closing = false;
  }

  async open(baudRate) {
    await this.port.open({ baudRate, bufferSize: 65536 });
    this.baudRate = baudRate;
    this.closing = false;
    this.writer = this.port.writable.getWriter();
    this.reading = this.readLoop();
  }

  async readLoop() {
    // framing, parity and overrun errors are not fatal: the port hands out a fresh stream, a lost port none
    let again = true;
    while (again && this.port.readable && !this.closing) {
      again = false;
      this.reader = this.port.readable.getReader();
      try {
        for (;;) {
          const { value, done } = await this.reader.read();
          if (done) {
            break;
          }
          if (value?.length) {
            this.chunks.push(value);
          }
        }
      } catch {
        again = true;
      } finally {
        this.reader.releaseLock();
      }
    }
  }

  // everything received since the last call
  take() {
    if (this.chunks.length === 0) {
      return new Uint8Array(0);
    }
    const total = this.chunks.reduce((n, c) => n + c.length, 0);
    const out = new Uint8Array(total);
    let at = 0;
    for (const c of this.chunks) {
      out.set(c, at);
      at += c.length;
    }
    this.chunks = [];
    return out;
  }

  discard() {
    this.chunks = [];
  }

  async write(bytes) {
    await this.writer.write(bytes);
  }

  async reopen(baudRate) {
    await this.close();
    await this.open(baudRate);
  }

  // for a device that stopped draining: its pending writes are dropped instead of waited for
  async abort() {
    try {
      await this.writer?.abort();
    } catch {
      // already gone
    }
    await this.close();
  }

  async close() {
    this.closing = true;
    try {
      await this.reader?.cancel();
    } catch {
      // already closed
    }
    await this.reading;
    try {
      this.writer?.releaseLock();
    } catch {
      // already released
    }
    try {
      await this.port.close();
    } catch {
      // already closed
    }
    this.reader = this.writer = this.reading = null;
  }
}

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
