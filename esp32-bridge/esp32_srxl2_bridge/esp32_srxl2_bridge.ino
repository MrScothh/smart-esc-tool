/*
 * SRXL2 wire adapter for an ESP32.
 *
 * Turns an ESP32 into the electrical half of a flight controller's SRXL2 port,
 * with the protocol driven from a PC over USB. The point is iteration speed: a
 * probe that guesses wrong costs a Python edit instead of a firmware flash.
 *
 * Why not a plain USB-TTL adapter. INAV opens the SRXL2 port with SERIAL_BIDIR,
 * which on STM32 is hardware single-wire half duplex: one pad, push-pull,
 * carrying both directions - see serial_uart_stm32h7xx.c, where the SERIAL_BIDIR
 * branch configures only the TX pin and configures it IOCFG_AF_PP. A USB-TTL
 * adapter has two separate pins and has to be bodged into that shape with a
 * series resistor, which is a different circuit from the one the firmware will
 * be driving. The ESP32's GPIO matrix can route a UART's transmit and receive to
 * the same pad, so it reproduces the flight controller's arrangement rather than
 * approximating it: the same single wire, driven the same way.
 *
 * It also answers as a flight controller. A PC configuring an ESC in an
 * aircraft reaches the wire through INAV's serial passthrough, not through an
 * adapter, and that path - MSP, the passthrough handshake, a port that stops
 * answering anything else until it sees +++ - is where a tool breaks. INAV has
 * no ESP32 port and never will (254 targets, all STM32 or AT32), so the next
 * best thing is to speak the flight controller's half of the conversation:
 * send this sketch MSP and it replies as INAV would, and after
 * MSP_SET_PASSTHROUGH it becomes a transparent pipe to the wire. The host side
 * being exercised is then the real one, over a real USB serial port.
 *
 * Which protocol is in use is decided by what arrives rather than by a mode
 * command: the adapter's ownframing is SLIP and MSP begins "$M<", so the two
 * cannot be confused, and neither end has to remember a setting.
 *
 * Everything above the wire stays on the PC. This sketch only:
 *   - owns the pad and the baud rate, including switching it once the transmit
 *     buffer has actually drained, which is the thing the oracle harness proved
 *     the driver depends on;
 *   - timestamps received bytes at the microsecond, so inter-frame gaps and
 *     turnaround times are measurable rather than inferred from USB arrival
 *     times, which Windows quantises to its latency timer;
 *   - suppresses the echo of our own transmissions, which a single wire returns
 *     by construction, and can report it separately so the host can confirm what
 *     reached the wire;
 *   - optionally repeats one frame on a timer, for when the ESC needs a cadence
 *     kept up while the host is busy thinking.
 */

#include <Arduino.h>
#include "driver/gpio.h"
#include "esp_rom_gpio.h"
#include "soc/uart_periph.h"

/*
 * The single wire.
 *
 * 18 rather than something lower because it has to be free on every ESP32 worth
 * using here. Off limits: the strapping pins (0, 2, 12, 15), the flash pins
 * (6..11), and - the one that is easy to miss - 16 and 17, which carry the
 * PSRAM chip select and clock on every WROVER module. Espressif do not merely
 * reserve those two, they usually do not bring them out to the header at all,
 * so a wire soldered to "17" on a WROVER board is a wire soldered to nothing.
 * On a C3, which has far fewer pins, use 4 or 5.
 */
#ifndef WIRE_PIN
#define WIRE_PIN            18
#endif

/* Serial1. Needed by name because the GPIO matrix is driven by hand below. */
#define WIRE_UART           1

#define USB_BAUD            921600
#define WIRE_BAUD_DEFAULT   115200

#define RX_CHUNK_MAX        192     /* bytes per report; an SRXL2 frame is <= 80 */
#define RX_IDLE_FLUSH_US    200     /* report a partial chunk after this much silence */
#define CMD_MAX             256

/* The three things this port can be doing. */
#define HOST_SLIP           0       /* the adapter's own protocol */
#define HOST_MSP            1       /* answering as a flight controller */
#define HOST_PIPE           2       /* raw bytes, both ways, until +++ */

#define MSP_MAX             128
#define MSP_STALE_US        250000      /* a half finished request is abandoned */
#define MSP_FC_VARIANT      2
#define MSP_FC_VERSION      3
#define MSP_API_VERSION     1
#define MSP_BOARD_INFO      4
#define MSP_SET_PASSTHROUGH 245
#define PASSTHROUGH_BY_FUNCTION 0xFE

/*
 * The escape, and why it needs the silence in front of it.
 *
 * Three plus signs are five bytes of ordinary SRXL2 away from happening by
 * accident, so INAV asks for a guard interval first and this does the same: a
 * second with nothing on the port, then +++. Without the guard a channel value
 * of 0x2B2B would eventually end the session on its own.
 */
#define ESCAPE_GUARD_US     1000000
#define KEEPALIVE_MAX       80

/* SLIP, RFC 1055. Chosen because a resync costs one byte and no state. */
#define SLIP_END            0xC0
#define SLIP_ESC            0xDB
#define SLIP_ESC_END        0xDC
#define SLIP_ESC_ESC        0xDD

static uint32_t echoPending  = 0;   /* bytes we transmitted that must come back */
static bool     reportEcho   = false;
static bool     pwmActive    = false;

static uint8_t  keepalive[KEEPALIVE_MAX];
static uint8_t  keepaliveLen    = 0;
static uint32_t keepalivePeriod = 0;    /* microseconds, 0 = off */
static uint32_t keepaliveLast   = 0;

static uint8_t  cmd[CMD_MAX];
static uint16_t cmdLen  = 0;
static bool     cmdEsc  = false;

static uint8_t  hostMode = HOST_SLIP;
static uint8_t  msp[MSP_MAX];
static uint16_t mspLen  = 0;            /* bytes of the request collected */
static uint16_t mspWant = 0;            /* how many the header says there are */
static uint32_t pipeLastByteUs = 0;     /* for the escape's guard interval */
static uint8_t  pipePluses = 0;
static uint32_t mspLastUs = 0;          /* to abandon a request that stopped */
static uint8_t  recent[3] = {0, 0, 0};  /* rolling window, to spot "$M<" */

/*---------------------------------------------------------------------------
 * USB framing
 *-------------------------------------------------------------------------*/

static void slipByte(uint8_t b)
{
    switch (b) {
    case SLIP_END: Serial.write(SLIP_ESC); Serial.write(SLIP_ESC_END); break;
    case SLIP_ESC: Serial.write(SLIP_ESC); Serial.write(SLIP_ESC_ESC); break;
    default:       Serial.write(b);                                    break;
    }
}

static void sendFrame(uint8_t tag, uint32_t stamp, const uint8_t *data, size_t len)
{
    Serial.write(SLIP_END);
    slipByte(tag);
    for (int i = 0; i < 4; i++) {
        slipByte((uint8_t)(stamp >> (8 * i)));
    }
    for (size_t i = 0; i < len; i++) {
        slipByte(data[i]);
    }
    Serial.write(SLIP_END);
}

static void sendText(uint8_t tag, const char *text)
{
    sendFrame(tag, micros(), (const uint8_t *)text, strlen(text));
}

/*---------------------------------------------------------------------------
 * The wire
 *-------------------------------------------------------------------------*/

static void wireBegin(uint32_t baud)
{
    /*
     * Serial1's default pins are the flash pins on a classic ESP32, so they are
     * always given explicitly. Passing the same pin twice asks the GPIO matrix
     * for transmit and receive on one pad.
     */
    Serial1.begin(baud, SERIAL_8N1, WIRE_PIN, WIRE_PIN);

    /*
     * Leave the pad released, listening, held up by the internal pull-up.
     *
     * The pad must not drive except while we are transmitting. Both ends of this
     * wire are push-pull, and the protocol is strictly turn-taking, so an idle
     * transmitter parked high would be fighting the ESC's low every time it
     * answered - two output drivers on one node, and a received byte that is
     * neither a clean high nor a clean low. An STM32 does not have this problem
     * because its hardware half-duplex releases the pin whenever it is not
     * sending; wireWrite() below does the same thing by hand.
     *
     * The pull-up only has to hold the idle level, never to shape an edge: while
     * either end is sending, that end is driving both directions. So the weak
     * internal one is enough even at 400000, and no external resistor is needed.
     */
    gpio_set_pull_mode((gpio_num_t)WIRE_PIN, GPIO_PULLUP_ONLY);
    gpio_set_direction((gpio_num_t)WIRE_PIN, GPIO_MODE_INPUT);
    esp_rom_gpio_connect_in_signal(WIRE_PIN,
                                   UART_PERIPH_SIGNAL(WIRE_UART, SOC_UART_RX_PIN_IDX),
                                   false);
}

/*
 * Take the wire, or give it back.
 *
 * The re-routing is not decoration. gpio_set_direction() enables the pad's
 * output by binding it to the plain GPIO output signal - the Arduino core does
 * the same thing in esp32-hal-uart.c, `esp_rom_gpio_connect_out_signal(pin,
 * SIG_GPIO_OUT_IDX, ...)` - which quietly unhooks the UART from the pad. A pad
 * left like that sits at whatever the GPIO output register holds, which is low,
 * so the receiver reads one framing error and nothing else. That is exactly what
 * the self test caught: fourteen bytes sent, a single 0x00 back.
 *
 * So the transmit signal is bound again after every direction change. Releasing
 * needs no such care, because turning the output off leaves the receive routing
 * alone.
 */
static void wireDrive(bool driving)
{
    if (driving) {
        gpio_set_direction((gpio_num_t)WIRE_PIN, GPIO_MODE_INPUT_OUTPUT);
        esp_rom_gpio_connect_out_signal(WIRE_PIN,
                                        UART_PERIPH_SIGNAL(WIRE_UART, SOC_UART_TX_PIN_IDX),
                                        false, false);
    } else {
        gpio_set_direction((gpio_num_t)WIRE_PIN, GPIO_MODE_INPUT);
    }
}

/*
 * Drive the wire as an ordinary servo output instead of a serial port.
 *
 * Not part of the protocol - a continuity test that the ESC itself answers.
 * A self test can only prove the loopback inside the chip: the echo comes back
 * through the GPIO matrix whether or not the pad reaches anything, so a pin
 * that is reserved, unbonded or simply not the one the wire is on passes it and
 * teaches nothing. An ESC with no valid signal beeps, and an ESC that starts
 * seeing 1000 us pulses stops and arms. That change of tune is the only
 * evidence available that the wire goes where it is believed to go, and it does
 * not depend on getting the protocol right first.
 *
 * Clamped well inside the servo range, and the caller is expected to ask for
 * idle. The receive routing is put back on the way out, because attaching LEDC
 * takes the pad over.
 */
static void wirePwm(uint16_t us)
{
    if (us == 0) {
        if (pwmActive) {
            ledcDetach(WIRE_PIN);
            pwmActive = false;
            gpio_set_pull_mode((gpio_num_t)WIRE_PIN, GPIO_PULLUP_ONLY);
            gpio_set_direction((gpio_num_t)WIRE_PIN, GPIO_MODE_INPUT);
            esp_rom_gpio_connect_in_signal(WIRE_PIN,
                                           UART_PERIPH_SIGNAL(WIRE_UART, SOC_UART_RX_PIN_IDX),
                                           false);
        }
        return;
    }

    if (us < 900)  { us = 900;  }
    if (us > 2100) { us = 2100; }

    if (!pwmActive) {
        ledcAttach(WIRE_PIN, 50, 16);       /* 50 Hz, the servo frame rate */
        pwmActive = true;
    }
    ledcWrite(WIRE_PIN, (uint32_t)(((uint64_t)us * 65535u) / 20000u));
}

static void wireSetBaud(uint32_t baud)
{
    /*
     * Drain first. SRXL2 negotiates upwards during the handshake and both ends
     * switch after the reply has gone out; changing the divisor with bytes still
     * in the shift register sends the tail of that reply at the new rate, which
     * the ESC would see as a framing error at exactly the moment the link is
     * being established.
     */
    Serial1.flush();
    Serial1.updateBaudRate(baud);
}

static void wireWrite(const uint8_t *data, size_t len)
{
    if (pwmActive) {
        return;
    }

    /*
     * In passthrough the echo is left alone, on purpose.
     *
     * A flight controller's single wire returns the host's own bytes and the
     * host is expected to account for them; swallowing them here would make
     * this adapter easier to talk to than the thing it is imitating, and the
     * host code that will meet a real board would go untested. In the
     * adapter's own protocol the echo is still held back, because there the
     * host asked for frames, not for a wire.
     */
    if (hostMode != HOST_PIPE) {
        echoPending += len;
    }

    /*
     * Take the wire, send, wait for the last bit to actually leave, release it.
     *
     * flush() is what makes the release safe: dropping the driver while bytes are
     * still in the shift register would truncate the frame mid-character. It
     * blocks for as long as the frame takes - about 7 ms for a full one at 115200,
     * 2 ms at 400000 - which is of no consequence here and buys an exact echo
     * count, since everything we sent is back in the receive FIFO by the time it
     * returns.
     */
    wireDrive(true);
    Serial1.write(data, len);
    Serial1.flush();
    wireDrive(false);
}

/*---------------------------------------------------------------------------
 * Answering as a flight controller
 *-------------------------------------------------------------------------*/

static void mspReply(uint8_t cmd, const uint8_t *payload, uint8_t len)
{
    uint8_t head[5] = { '$', 'M', '>', len, cmd };
    uint8_t crc = len ^ cmd;
    for (uint8_t i = 0; i < len; i++) {
        crc ^= payload[i];
    }
    Serial.write(head, 5);
    if (len) {
        Serial.write(payload, len);
    }
    Serial.write(&crc, 1);
    Serial.flush();
}

/*
 * Only the questions a configuration tool actually asks.
 *
 * Identifying as INAV is not a pretence about being a flight controller: it is
 * the answer that makes a host take the path it would take with one, which is
 * the path being tested. Anything not answered here is simply ignored, the way
 * a board ignores a command it was built without.
 */
static void handleMsp(uint8_t cmd, const uint8_t *payload, uint8_t len)
{
    switch (cmd) {
    case MSP_API_VERSION: {
        const uint8_t v[3] = { 0, 2, 5 };
        mspReply(cmd, v, sizeof(v));
        break;
    }
    case MSP_FC_VARIANT:
        mspReply(cmd, (const uint8_t *)"INAV", 4);
        break;
    case MSP_FC_VERSION: {
        const uint8_t v[3] = { 9, 1, 0 };
        mspReply(cmd, v, sizeof(v));
        break;
    }
    case MSP_BOARD_INFO: {
        uint8_t info[9] = { 'E', 'S', 'P', '2', 0, 0, 0, 0, 0 };
        mspReply(cmd, info, sizeof(info));
        break;
    }
    case MSP_SET_PASSTHROUGH: {
        /*
         * A real board answers 0 here when nothing has opened the port for that
         * function, which is worth reproducing: a tool that only ever sees 1 is
         * never tested against the refusal. Here the wire is always open, so
         * the only refusal is for a mode this adapter does not implement.
         */
        uint8_t ok = 0;
        if (len >= 1 && payload[0] == PASSTHROUGH_BY_FUNCTION) {
            ok = 1;
        }
        mspReply(cmd, &ok, 1);
        if (ok) {
            hostMode = HOST_PIPE;
            pipeLastByteUs = micros();
            pipePluses = 0;
            echoPending = 0;
            while (Serial1.available()) {
                Serial1.read();
            }
        }
        break;
    }
    default:
        break;                              /* a board ignores what it lacks */
    }
}

/*---------------------------------------------------------------------------
 * Host commands
 *-------------------------------------------------------------------------*/

static void handleCommand(const uint8_t *p, uint16_t len)
{
    if (len < 1) {
        return;
    }

    switch (p[0]) {
    case '?':
        sendText('I', "srxl2-bridge 2");
        break;

    case 'B':
        if (len >= 5) {
            wireSetBaud((uint32_t)p[1] | ((uint32_t)p[2] << 8) |
                        ((uint32_t)p[3] << 16) | ((uint32_t)p[4] << 24));
            sendFrame('B', micros(), NULL, 0);      /* switched, and when */
        }
        break;

    case 'W':
        wireWrite(p + 1, len - 1);
        break;

    case 'K': {
        if (len >= 3) {
            uint16_t ms  = (uint16_t)p[1] | ((uint16_t)p[2] << 8);
            uint16_t n   = len - 3;
            keepaliveLen = (uint8_t)(n > KEEPALIVE_MAX ? KEEPALIVE_MAX : n);
            memcpy(keepalive, p + 3, keepaliveLen);
            keepalivePeriod = (uint32_t)ms * 1000;
            keepaliveLast   = micros();
        }
        break;
    }

    case 'P':
        if (len >= 3) {
            wirePwm((uint16_t)p[1] | ((uint16_t)p[2] << 8));
            sendFrame('P', micros(), NULL, 0);
        }
        break;

    case 'E':
        reportEcho = (len >= 2 && p[1]);
        break;

    case 'X':
        /*
         * Drop whatever is in flight and forget the echo we are still owed. The
         * host uses this when a probe went wrong and the byte counts no longer
         * line up; without it a single lost byte would shift every later report.
         */
        while (Serial1.available()) {
            Serial1.read();
        }
        echoPending     = 0;
        keepalivePeriod = 0;
        sendFrame('X', micros(), NULL, 0);
        break;

    default:
        sendText('!', "unknown command");
        break;
    }
}

/*---------------------------------------------------------------------------
 *-------------------------------------------------------------------------*/

void setup()
{
    Serial.begin(USB_BAUD);
    wireBegin(WIRE_BAUD_DEFAULT);
    sendText('I', "srxl2-bridge 2 ready");
}

void loop()
{
    /* --- USB in ------------------------------------------------------- */
    if (hostMode == HOST_PIPE) {
        /*
         * Raw both ways. Bytes are gathered and handed to the wire in one call
         * rather than one at a time, because a single wire is taken and
         * released around each transmission and doing that per byte would put a
         * turnaround in the middle of every frame.
         */
        static uint8_t out[CMD_MAX];
        uint16_t n = 0;
        while (Serial.available() && n < CMD_MAX) {
            uint8_t b = Serial.read();
            uint32_t now = micros();

            if (b == '+' && (pipePluses || (now - pipeLastByteUs) > ESCAPE_GUARD_US)) {
                pipePluses++;
                pipeLastByteUs = now;
                if (pipePluses >= 3) {
                    hostMode = HOST_SLIP;
                    cmdLen = 0;
                    cmdEsc = false;
                    pipePluses = 0;
                    sendText('I', "passthrough closed");
                    return;
                }
                continue;               /* held back: it may be the escape */
            }
            if (pipePluses) {
                /* not the escape after all - put the plus signs back */
                for (uint8_t i = 0; i < pipePluses && n < CMD_MAX; i++) {
                    out[n++] = '+';
                }
                pipePluses = 0;
            }
            pipeLastByteUs = now;
            out[n++] = b;
        }
        if (n) {
            wireWrite(out, n);
        }
    } else while (Serial.available()) {
        uint8_t b = Serial.read();

        recent[0] = recent[1];
        recent[1] = recent[2];
        recent[2] = b;
        bool mspStart = (recent[0] == '$' && recent[1] == 'M' && recent[2] == '<');

        if (mspStart) {
            /*
             * Start of an MSP request, wherever it falls.
             *
             * Looking only at the first byte of a command was not enough. A
             * host that has not yet found this adapter's USB rate talks at the
             * wrong one first, and what lands is noise; noise with no SLIP
             * frame terminator in it leaves the command buffer part filled,
             * and from then on nothing is ever "the first byte" again. The
             * three character opening is unambiguous - the adapter's own
             * commands are single letters - so it is recognised as a sequence
             * and whatever partial command preceded it is dropped.
             */
            cmdLen = 0;
            cmdEsc = false;
            hostMode = HOST_MSP;
            mspLen = 3;
            mspWant = 0;
            mspLastUs = micros();
            msp[0] = '$'; msp[1] = 'M'; msp[2] = '<';
            continue;
        }

        if (hostMode == HOST_MSP) {
            /*
             * Continuing a request whose opening was recognised above.
             */
            /*
             * Nothing here may be able to wedge.
             *
             * The host may well be talking at the wrong rate - it does not know
             * this adapter's USB speed until something answers - and what
             * arrives is then noise. Noise contains dollar signs, and a noisy
             * length byte can ask for more bytes than will ever come. A parser
             * that simply waits for them swallows every later request, and the
             * port looks dead when it is merely stuck. So: an oversized length
             * is rejected outright, and a request that stops mid-way is
             * abandoned after a quarter of a second.
             */
            uint32_t now = micros();
            if (mspLen && (now - mspLastUs) > MSP_STALE_US) {
                mspLen = 0;
                mspWant = 0;
            }
            mspLastUs = now;
            hostMode = HOST_MSP;

            if (mspLen >= MSP_MAX) {
                mspLen = 0;
                mspWant = 0;
                hostMode = HOST_SLIP;
                continue;
            }
            msp[mspLen++] = b;

            if (mspLen == 4) {
                if (msp[3] > MSP_MAX - 6) {     /* cannot be a real request */
                    mspLen = 0;
                    mspWant = 0;
                    hostMode = HOST_SLIP;
                } else {
                    mspWant = 6 + msp[3];       /* header, payload, crc */
                }
            } else if (mspWant && mspLen >= mspWant) {
                handleMsp(msp[4], msp + 5, msp[3]);
                mspLen = 0;
                mspWant = 0;
                if (hostMode == HOST_MSP) {
                    hostMode = HOST_SLIP;   /* back to neutral between requests */
                }
            }
            continue;
        }

        if (b == SLIP_END) {
            if (cmdLen) {
                handleCommand(cmd, cmdLen);
            }
            cmdLen = 0;
            cmdEsc = false;
            continue;
        }
        if (b == SLIP_ESC) {
            cmdEsc = true;
            continue;
        }
        if (cmdEsc) {
            b = (b == SLIP_ESC_END) ? SLIP_END : (b == SLIP_ESC_ESC) ? SLIP_ESC : b;
            cmdEsc = false;
        }
        if (cmdLen < CMD_MAX) {
            cmd[cmdLen++] = b;
        }
    }

    /* --- wire in ------------------------------------------------------ */
    static uint8_t  rx[RX_CHUNK_MAX];
    static uint8_t  echo[RX_CHUNK_MAX];
    static uint16_t rxLen = 0, echoLen = 0;
    static uint32_t rxFirstUs = 0, echoFirstUs = 0, lastByteUs = 0;

    while (Serial1.available()) {
        uint32_t now = micros();
        uint8_t  b   = Serial1.read();
        lastByteUs   = now;

        if (echoPending) {
            /*
             * Our own transmission coming back off the single wire. Held rather
             * than discarded outright: when the host asks for it, comparing it
             * against what was sent is the only direct evidence of what the pad
             * actually drove.
             */
            echoPending--;
            if (reportEcho) {
                if (!echoLen) {
                    echoFirstUs = now;
                }
                echo[echoLen++] = b;
                if (echoLen == RX_CHUNK_MAX) {
                    sendFrame('E', echoFirstUs, echo, echoLen);
                    echoLen = 0;
                }
            }
            continue;
        }

        if (hostMode == HOST_PIPE) {
            Serial.write(b);            /* no framing: the host wants the wire */
            continue;
        }

        if (!rxLen) {
            rxFirstUs = now;
        }
        rx[rxLen++] = b;
        if (rxLen == RX_CHUNK_MAX) {
            sendFrame('R', rxFirstUs, rx, rxLen);
            rxLen = 0;
        }
    }

    /*
     * Flush on a gap rather than on a frame boundary. The bridge deliberately
     * knows nothing about SRXL2 framing - the host decides what a frame is - so
     * the only thing worth reporting here is where the silences fell.
     */
    if ((rxLen || echoLen) && (micros() - lastByteUs) > RX_IDLE_FLUSH_US) {
        if (echoLen) {
            sendFrame('E', echoFirstUs, echo, echoLen);
            echoLen = 0;
        }
        if (rxLen) {
            sendFrame('R', rxFirstUs, rx, rxLen);
            rxLen = 0;
        }
    }

    /* --- keepalive ---------------------------------------------------- */
    if (keepalivePeriod && keepaliveLen &&
        (micros() - keepaliveLast) >= keepalivePeriod) {
        keepaliveLast = micros();
        wireWrite(keepalive, keepaliveLen);
    }
}
