# hello

A ZETA "Hello World" that runs **without any hardware or cloud account**.

A ZETA network ends at the ZETA Server, and applications reach devices only
through its RESTful API (JSON over HTTP, with signed tokens and encrypted
request parameters). So an application can be written and tested against a
fake server that answers the very same endpoints a real one would.

```
┌──────────────────────────────────────────────────────────────┐
│ Real deployment                      │ This example          │
│                                      │                       │
│  [ application ]                     │  app.py               │
│           ▲                          │      ▲                │
│           │ HTTPS, RESTful API       │      │ HTTP, same API │
│           ▼                          │      ▼                │
│  [ ZETA Server ] (cloud or on-prem)  │  server_mock.py       │
│           ▲                          │  fakes all of this    │
│           │ 4G / Ethernet            │                       │
│  [ AP ] (base station)               │                       │
│           ▲                          │                       │
│           │ 920MHz UNB, up to 4 hops │                       │
│  [ Mote ] (battery-powered relay)    │                       │
│           ▲                          │                       │
│           │ 920MHz UNB               │                       │
│  [ module ] (end device)             │                       │
│           ▲                          │                       │
│           │ UART, AT commands        │                       │
│  [ user MCU / sensor ]               │                       │
└──────────────────────────────────────────────────────────────┘
```

`app.py` is the part you keep when real hardware arrives: point it at a real
ZETA Server and it works unchanged.

## Run it

Two terminals. There is no broker to start: the API is plain HTTP.

```bash
uv sync

# 1. the fake ZETA Server (it starts "receiving" uplinks right away)
uv run server_mock.py

# 2. the application
uv run app.py
```

`app.py` prints something like:

```
[app] connected to http://localhost:8080/teamcms/ws/, modules: 88880025
[app] downlink queued for 88880025 as msgid 1546546
[app] uplink from 88880025 via ap D0000011: b'hello #2'
[app] downlink delivered to 88880025 at 2026-09-30 14:14:58
[app] uplink from 88880025 via ap D0000011: b'hello #3'
```

## What happens

All paths are relative to `http://<host>:<port>/teamcms/ws/`.

| Step | Request | Response `data` |
|---|---|---|
| 1. get a token | `GET auth_v1/auth_token/query/getWanAccessToken?api_key=..&signal=..` | `[{"access_token": ".."}]`, valid for 5 minutes |
| 2. find devices | `GET zeta_v1/wan_ms/query/{api_key}/getMsList` | `[{"uid": "88880025"}]` |
| 3. send a downlink | `POST zeta_v1/wan_ms/control/{uid}/newCtrlMsPassthrough` with `{"data": "<hex>"}` | `[{"msgid": 1546546}]` — **queued**, not delivered |
| 4. poll uplinks | `POST zeta_v1/wan_ms/query/{uid}/getMsUploadDataByDate` with `{"starttime", "endtime"}` | `[{"objectid", "apuid", "updata": "<hex>", "uptime", ...}]` |
| 5. poll downlink state | `POST zeta_v1/wan_ms/query/{uid}/getMsCtlHistoryByDate` with `{"starttime", "endtime"}` | `[{"type": 8, "status": 300 → 301, "downdata", ...}]` |

Every response has the same envelope, and errors come back as HTTP 200 with a
non-zero `status`:

```json
{"data": [...], "errmsg": "", "status": 0, "ts": 1790745300}
```

Details worth noticing:

- **The application pulls; nothing is pushed.** There is no subscription.
  Uplinks are fetched by time range, so the app has to remember where it left
  off, overlap windows a little and deduplicate by `objectid`.
- **Time ranges must stay within one day.** `starttime` and `endtime` have to
  fall on the same (server-local) day, so a window crossing midnight is split
  in two (`same_day_windows` in `app.py`).
- **A downlink is a request, not a delivery.** The send only returns a
  `msgid`. A sleeping module listens only right after it transmits, so the
  server holds the command (`status` 300, pending) until the device's next
  uplink and then marks it done (301). The mock reproduces that delay.
- **The `msgid` does not come back in the history.** The history lists
  commands by `type`, `downdata` and times, so `app.py` matches on the payload.
- **Payloads are hex strings.** `updata` and `data` are opaque bytes for the
  user MCU; both ends agree on what they mean, just as with Wirepas endpoints.
- **Only requests are encrypted.** See below.

## Authentication and encryption

Both are defined in the specification and implemented in
[common.py](common.py):

- **Token**: `signal = hex(HMAC-SHA1(key=secret_key, msg=api_key))`. The token
  is then passed as `access_token` on every call and expires after 5 minutes;
  a `10003` status means "get a new one", which `app.py` does and retries.
- **Request parameters**: JSON → `AES/CFB8/NoPadding` keyed with the
  `secret_key` (32 ASCII characters, so AES-256) and a random 16-character IV
  → base64 of `IV + ciphertext`. The POST body is that base64 string.
- **Responses** are plain JSON.

The default `api_key` / `secret_key` are the sample values printed in the
specification, so the output is checkable against the document:

```bash
uv run python -c "from common import *; print(sign(API_KEY, SECRET_KEY)); print(encrypt('{\"data\":\"889954\"}', SECRET_KEY, iv='123456789abcdef0'))"
# 8d1d64adc49520b07e2ab0fc915cff90360e45dc
# MTIzNDU2Nzg5YWJjZGVmMEqvMCIL3enskNjSphOfYjhI
```

Both lines match the specification's examples byte for byte.

## Files

| File | Role |
|---|---|
| [app.py](app.py) | The application — hardware-independent. |
| [server_mock.py](server_mock.py) | Fake ZETA Server: fakes the module, Mote, AP and server. |
| [common.py](common.py) | Settings, identifiers, paths, signing and encryption shared by both. |

`ZETA_BASE_URL`, `ZETA_API_KEY` and `ZETA_SECRET_KEY` override the defaults,
e.g. to point `app.py` at a real server:

```bash
ZETA_BASE_URL=https://www.zetacloud.com:25455/teamcms/ws/ \
ZETA_API_KEY=... ZETA_SECRET_KEY=... uv run app.py
```

## Dependencies

- [`httpx`](https://pypi.org/project/httpx/) — the HTTP client.
- [`cryptography`](https://pypi.org/project/cryptography/) — AES-CFB8. (CFB8
  now lives in `cryptography.hazmat.decrepit`; it is old, but it is what the
  API uses.)

The fake server uses only the standard library's `http.server`.

## What the mock does not do

- Only the five endpoints above. The specification also covers device details
  and status (RSSI, battery, registration), heartbeats, Motes, APs, multicast
  and broadcast.
- One module behind one Mote and one AP, with a fixed route.
- No HTTPS, and no checks beyond the `api_key`, the signature and the token.
