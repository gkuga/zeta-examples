# zeta-examples

Small, self-contained experiments for getting familiar with
[ZETA](https://www.zeta-alliance.org/), an LPWAN from ZiFiSense, and its
server API. Each directory is a uv project.

```
  [ application ]
           ▲ HTTPS, RESTful API        ← hello: fakes everything below this
  [ ZETA Server ]
           ▲ 4G / Ethernet
  [ AP ] (base station)
           ▲ 920MHz UNB, up to 4 hops through Motes
  [ Mote ] (relay)
           ▲ 920MHz UNB
  [ module ] (end device)
           ▲ UART, AT commands
  [ user MCU / sensor ]
```

## Examples

| Name | Fakes | Description |
|---|---|---|
| [hello](hello/) | everything below the ZETA Server API | An application and a fake ZETA Server exchanging uplinks and a downlink |

## How to run

This repo uses [uv](https://docs.astral.sh/uv/). Enter an example directory and
follow its README.

```bash
cd hello
uv sync
```

## Notes on ZETA

- **UNB, not LoRa.** ZETA uses ultra-narrow-band channels (2 kHz wide) in the
  920MHz band in Japan, with rates from about 100 bps to 100 kbps.
- **Star plus mesh.** A module can reach an AP directly or through up to four
  battery-powered Motes, which form a self-healing relay tree.
- **Three protocols** trade latency against capacity: ZETA-P (low latency,
  low traffic), ZETA-S (scheduled, for dense urban networks) and ZETA-Lite
  (lighting control).
- **The stack is closed.** Unlike Wirepas, there is no open-source gateway to
  run and no gateway API to fake. The public boundary is the ZETA Server's
  RESTful API, so that is where these examples start.

## ZETA and Wirepas

Compared with [wirepas-examples](https://github.com/gkuga/wirepas-examples):

| | Wirepas | ZETA |
|---|---|---|
| Radio | 2.4GHz mesh, every node routes | 920MHz UNB, dedicated Motes relay |
| Where the network ends | your gateway, publishing to MQTT | the vendor's ZETA Server |
| Application API | MQTT + protobuf, pushed | HTTP + JSON, polled |
| Getting uplinks | subscribe to a topic | query by time range |
| Downlink result | response by `req_id` | command history by `status` |
| Auth | whatever your broker does | HMAC-signed token, AES-encrypted params |
| Payload addressing | endpoints (`src_ep` / `dst_ep`) | opaque hex, per device `uid` |

## References

- ZETA92JP LPWAN Platform RESTful API specification v2.11, Techsor —
  [PDF](https://techsor.co.jp/pdf/10001_02.pdf). The source for every path,
  field and code used here.
- Introduction to ZETA, Techsor, 2018 —
  [PDF](https://www.jasa.or.jp/dl/tech/ZETA_2018-06-29_printed.pdf).
  Architecture, Motes, protocols.
