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
    echoPending += len;

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
 * Host commands
 *-------------------------------------------------------------------------*/

static void handleCommand(const uint8_t *p, uint16_t len)
{
    if (len < 1) {
        return;
    }

    switch (p[0]) {
    case '?':
        sendText('I', "srxl2-bridge 1");
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
    sendText('I', "srxl2-bridge ready");
}

void loop()
{
    /* --- USB in ------------------------------------------------------- */
    while (Serial.available()) {
        uint8_t b = Serial.read();

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
