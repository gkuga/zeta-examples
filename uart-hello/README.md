# uart-hello

A ZETA end device that runs **without the module**.

The other public edge of a ZETA network is the UART between a ZETA module and
the user's MCU. TOPPAN's TZM902DP documents its frames in the user manual, so
an MCU program can be written and tested against a fake module on a
pseudo-terminal (PTY) that answers the very same frames.

This is [hello](../hello/) turned upside down: there the fake covers
everything *below* the application; here it covers everything *above* the MCU.

```
┌──────────────────────────────────────────────────────────────┐
│ Real deployment                      │ This example          │
│                                      │                       │
│  [ application ]                     │                       │
│           ▲ HTTPS, RESTful API       │                       │
│  [ ZETA Server ]                     │  module_mock.py       │
│           ▲ 4G / Ethernet            │  fakes all of this    │
│  [ AP ] (base station)               │                       │
│           ▲ 920MHz UNB, 300 bps      │                       │
│  [ Mote ] (relay)                    │                       │
│           ▲                          │                       │
│  [ TZM902DP module ]                 │      ▲                │
│           ▲ UART 115200 8N1          │      │ PTY, same frames
│           ▼ + WAKEUP / INT pins      │      ▼                │
│  [ user MCU / sensor ]               │  mcu.py               │
└──────────────────────────────────────────────────────────────┘
```

`mcu.py` is the part you keep when a real module arrives: point it at the
serial port the module is wired to and it speaks the same frames.

## Run it

Two terminals, in this order.

```bash
uv sync

# 1. the fake module: creates a PTY and links it as ./module.tty
uv run module_mock.py

# 2. the MCU program
uv run mcu.py --test-mode
```

Then type a line into the module's terminal. It is queued as a downlink "from
the server" and reaches the MCU after the module's next transmission.

`mcu.py` prints something like:

```
[mcu] opened module.tty
[mcu] module status: UNREGISTERED
[mcu] module status: REGISTERED
[mcu] version: 00 09 01 02
[mcu] MAC: 88880025
[mcu] network time: 2026-09-30 14:43:16
[mcu] RSSI: -72 dBm
[mcu] test mode: SET_SUCCEED
[mcu] handed to module: b'hello #1'
[mcu] downlink: b'hello from the network'
[mcu] handed to module: b'hello #2'
```

and `module_mock.py`:

```
[module] listening on /dev/ttys018 (linked as ./module.tty)
[module] registered with the network
[module] heartbeat interval set to 10 s
[module] uplink b'hello #1' sent over the air
hello from the network
[module] downlink queued, waits for the next transmission: b'hello from the network'
[module] heartbeat sent over the air
[module] downlink to MCU: b'hello from the network'
```

## The frames

Every frame, in both directions:

```
FA F5 | Length | Type | Payload (0..50) | CRC16 (big-endian)
```

`Length` counts Type + Payload + CRC, and the CRC is CRC-16/XMODEM over
Length, Type and Payload. For example, sending the five bytes `11 22 33 44 55`:

```
FA F5 08 02 11 22 33 44 55 3B 6D
```

| Direction | Type | Frame | Answer |
|---|---|---|---|
| MCU → module | `02` | Send data (1–50 bytes) | `01` ACK, `02` buffer full, `03` length error, `04` CRC error, `43` unregistered |
| MCU → module | `00` | Inquire version | `00` + 4 bytes |
| MCU → module | `10` | Inquire MAC | `10` + 4 bytes |
| MCU → module | `11` | Inquire network time | `11` + year (2), month, day, hour, minute, second |
| MCU → module | `13` | Inquire network quality | `13` + RSSI magnitude (`48` = -72 dBm) |
| MCU → module | `14` | Inquire module status | `43` unregistered, `44` registered, `45`/`46` long/short sleep + remaining time |
| MCU → module | `22` | Set test mode (`00` heartbeat 6 h, `01` 10 s) | `20` succeeded, `21` failed |
| module → MCU | `30` | Downlink data (unsolicited) | — |

Every example frame in the manual was checked against this CRC, and all of
them match.

## What happens

- **An ACK is not a delivery.** `01` only means the module took the data into
  its buffer. The radio sends it later, and at 300 bps a full 50-byte frame
  spends over a second on air. Send faster than that and the buffer fills
  (`02`). The manual says to resend on any other answer or on silence, and
  `mcu.py` retries three times.
- **Downlinks ride on uplinks.** The module's default is *ACK downlink*: it
  listens only right after it transmits, which is what lets it sleep at 5 µA.
  A downlink typed into the mock waits for the next uplink or heartbeat. That
  is the same `status` 300 → 301 wait that [hello](../hello/) shows from the
  server side. *Real-time downlink* (2 mA standby) is switched from the
  ZETA Server and is not modelled.
- **Test mode is about heartbeats.** The module sends a heartbeat every 6
  hours; test mode makes it every 10 seconds, so downlinks arrive even when
  the MCU sends nothing.
- **Downlinks arrive at any time.** A `30` frame can show up while the MCU is
  waiting for the answer to something else, so every read in `mcu.py` goes
  through one function that prints downlinks and returns everything else.
- **The MAC and the server's uid are the same size.** The module's MAC is 4
  bytes, and the ZETA Server API names a module with 8 hex digits. The mock
  uses `88880025` for both, matching hello. That they are the same identifier
  is likely but not stated in either document.

## What a PTY cannot show

The real module has two more wires, and a PTY has neither:

| Pin | Direction | Meaning |
|---|---|---|
| WAKEUP | MCU → module | Drive low at least 10 ms before sending; the module sleeps otherwise |
| INT | module → MCU | Goes high 50 ms before a downlink frame, so the MCU can sleep too |

`mcu.py` keeps an empty `wakeup()` where the GPIO code would go. Without the
WAKEUP pin to mark a frame, the mock ends a frame after 20 ms of silence
instead; that is also how it tells a length error from a partial read.

## Where the mock guesses

The manual leaves some things out. The mock's choices:

- An unregistered module answers `43` to *time* and *quality* inquiries too
  (the manual only shows this for *send* and *status*).
- An empty payload or an unknown type gets `03` or no answer.
- The version bytes are `00 09 01 02`; the manual does not explain them.
- The buffer holds two frames.

And three errors in the manual:

- Before v1.3 the *send* type was given as `01`. v1.3 corrected it to `02`,
  but some examples still show `01`. `01` is the ACK in the other direction.
- The *Set Test mode* answers are printed with an `FF F5` preamble. The CRCs
  match `FA F5`, which the CRC does not cover, so this looks like a typo.
- The *CRC error* example frame actually has a correct CRC.

## Files

| File | Role |
|---|---|
| [mcu.py](mcu.py) | The MCU program — the part you keep. |
| [module_mock.py](module_mock.py) | Fake TZM902DP: fakes the module, radio, Mote, AP and server. |
| [frame.py](frame.py) | Frame types, encoding, decoding and the CRC, shared by both. |

To use a real module, pass its port:

```bash
uv run mcu.py /dev/ttyAMA0
```

Speed and framing already match the manual. What is left is the WAKEUP and
INT GPIO handling.

## Dependencies

- [`pyserial`](https://pypi.org/project/pyserial/) — used by `mcu.py`. It
  opens a PTY exactly like a real serial port.

The fake module uses only the standard library (`os.openpty`, `tty`, `select`).
It needs a Unix-like OS for the PTY.

## Reference

- ZETA wireless module TZM902DP user manual v1.3, TOPPAN —
  [PDF](https://www.toptdc.toppan.com/global-data/20230414180127858.pdf),
  section 4.
- [TomonobuHayakawa/TZM902DP](https://github.com/TomonobuHayakawa/TZM902DP) —
  an Arduino library for the Spresense add-on board, useful to see how the
  frames are used in practice.
