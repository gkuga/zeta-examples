"""A fake ZETA Server.

It stands in for the whole hardware side of the network -- the end device, the
Mote (relay), the AP (base station) and the ZETA Server itself -- and answers
the same RESTful API a real ZETA Server does. That is enough to develop and
test an application without any hardware or a cloud account.

    uv run server_mock.py
"""

import itertools
import json
import re
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from common import (
    AP_HOST_UID,
    AP_UID,
    API_KEY,
    BASE_URL,
    CMD_DONE,
    CMD_PENDING,
    CMD_TYPE_PASSTHROUGH,
    MODULE_UID,
    MOTE_UID,
    SECRET_KEY,
    STATUS_AUTH_FAILED,
    STATUS_BAD_PARAMETER,
    STATUS_NO_TOKEN,
    STATUS_OK,
    STATUS_TOKEN_EXPIRED,
    TOKEN_LIFETIME_S,
    decrypt,
    sign,
)

UPLINK_INTERVAL_S = 5

PREFIX = urlsplit(BASE_URL).path  # "/teamcms/ws/"


class Network:
    """What the server knows: issued tokens, received uplinks, queued downlinks."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.tokens: dict[str, float] = {}  # access_token -> expiry
        self.uplinks: list[dict] = []
        self.downlinks: list[dict] = []
        self.msgids = itertools.count(1546546)

    def issue_token(self) -> str:
        token = uuid.uuid4().hex
        with self.lock:
            self.tokens[token] = time.time() + TOKEN_LIFETIME_S
        return token

    def token_valid(self, token: str) -> bool:
        with self.lock:
            return self.tokens.get(token, 0) > time.time()

    def receive_uplink(self, payload: bytes) -> None:
        """Pretend the module's packet came up through the Mote and the AP.

        This is also the moment a ZETA module listens for downlinks: a sleeping
        device only opens its receiver right after it has transmitted, so the
        server holds downlinks until then.
        """
        now = int(time.time())
        with self.lock:
            self.uplinks.append(
                {
                    "objectid": uuid.uuid4().hex,
                    "msguid": AP_HOST_UID,
                    "apuid": AP_UID,
                    "updata": payload.hex(),
                    **timestamp_fields("aptime", now),
                    **timestamp_fields("uptime", now),
                }
            )
            pending = [d for d in self.downlinks if d["status"] == CMD_PENDING]
            for downlink in pending:
                downlink["status"] = CMD_DONE
                downlink.update(timestamp_fields("aptime", now))
                downlink.update(timestamp_fields("uptime", now))
        print(f"[server] uplink from {MODULE_UID} via {MOTE_UID} -> {AP_UID}: {payload!r}")
        for downlink in pending:
            data = bytes.fromhex(downlink["downdata"])
            print(f"[server] downlink delivered to {MODULE_UID}: {data!r}")

    def queue_downlink(self, uid: str, data_hex: str) -> int:
        now = int(time.time())
        with self.lock:
            msgid = next(self.msgids)
            self.downlinks.append(
                {
                    "objectid": uuid.uuid4().hex,
                    "uid": uid,
                    "msguid": AP_HOST_UID,
                    "apuid": AP_UID,
                    "type": CMD_TYPE_PASSTHROUGH,
                    "status": CMD_PENDING,
                    "downdata": data_hex,
                    **timestamp_fields("downtime", now),
                }
            )
        return msgid

    def uplinks_between(self, start: int, end: int) -> list[dict]:
        with self.lock:
            return [u for u in self.uplinks if start <= u["uptimeStamp"] <= end]

    def downlinks_between(self, start: int, end: int) -> list[dict]:
        with self.lock:
            return [dict(d) for d in self.downlinks if start <= d["downtimeStamp"] <= end]


network = Network()


def timestamp_fields(name: str, ts: int) -> dict:
    """The API reports every time twice: server-local text and unix seconds."""
    return {
        name: datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
        f"{name}Stamp": ts,
    }


def envelope(status: int, data: list | None = None, errmsg: str = "") -> dict:
    return {"data": data or [], "errmsg": errmsg, "status": status, "ts": int(time.time())}


class ApiError(Exception):
    def __init__(self, status: int, errmsg: str) -> None:
        self.status = status
        self.errmsg = errmsg


def time_range(params: dict) -> tuple[int, int]:
    """Read starttime/endtime, which must fall on the same (server-local) day."""
    try:
        start, end = int(params["starttime"]), int(params["endtime"])
    except (KeyError, TypeError, ValueError):
        raise ApiError(STATUS_BAD_PARAMETER, "ERROR REQUEST")
    if datetime.fromtimestamp(start).date() != datetime.fromtimestamp(end).date():
        raise ApiError(STATUS_BAD_PARAMETER, "starttime and endtime must be on the same day")
    return start, end


def get_token(query: dict) -> list:
    api_key = query.get("api_key", [""])[0]
    signal = query.get("signal", [""])[0]
    if api_key != API_KEY or signal != sign(API_KEY, SECRET_KEY):
        raise ApiError(STATUS_AUTH_FAILED, "AUTH FAILED")
    return [{"access_token": network.issue_token()}]


def get_module_list(api_key: str, params: dict) -> list:
    if api_key != API_KEY:
        raise ApiError(STATUS_BAD_PARAMETER, "ERROR REQUEST")
    return [{"uid": MODULE_UID}]


def get_upload_data(uid: str, params: dict) -> list:
    start, end = time_range(params)
    return network.uplinks_between(start, end) if uid == MODULE_UID else []


def get_ctl_history(uid: str, params: dict) -> list:
    start, end = time_range(params)
    return network.downlinks_between(start, end) if uid == MODULE_UID else []


def passthrough(uid: str, params: dict) -> list:
    data_hex = params.get("data", "")
    try:
        data = bytes.fromhex(data_hex)
    except ValueError:
        raise ApiError(STATUS_BAD_PARAMETER, "data must be hex")
    if uid != MODULE_UID or not data:
        raise ApiError(STATUS_BAD_PARAMETER, "ERROR REQUEST")
    msgid = network.queue_downlink(uid, data_hex)
    print(f"[server] downlink queued for {uid} as msgid {msgid}: {data!r}")
    return [{"msgid": msgid}]


# (method, path pattern, handler). The handler gets the path parameter and the
# decrypted request parameters.
ROUTES = [
    ("GET", r"zeta_v1/wan_ms/query/(\w+)/getMsList", get_module_list),
    ("POST", r"zeta_v1/wan_ms/query/(\w+)/getMsUploadDataByDate", get_upload_data),
    ("POST", r"zeta_v1/wan_ms/query/(\w+)/getMsCtlHistoryByDate", get_ctl_history),
    ("POST", r"zeta_v1/wan_ms/control/(\w+)/newCtrlMsPassthrough", passthrough),
]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.dispatch("GET")

    def do_POST(self) -> None:
        self.dispatch("POST")

    def dispatch(self, method: str) -> None:
        url = urlsplit(self.path)
        if not url.path.startswith(PREFIX):
            self.send_error(404)
            return
        path = url.path.removeprefix(PREFIX)
        query = parse_qs(url.query)

        try:
            if method == "GET" and path == "auth_v1/auth_token/query/getWanAccessToken":
                data = get_token(query)
            else:
                data = self.call_api(method, path, query)
        except ApiError as err:
            self.reply(envelope(err.status, errmsg=err.errmsg))
            return
        if data is None:
            self.send_error(404)
            return
        self.reply(envelope(STATUS_OK, data))

    def call_api(self, method: str, path: str, query: dict) -> list | None:
        for route_method, pattern, handler in ROUTES:
            match = re.fullmatch(pattern, path)
            if route_method == method and match:
                break
        else:
            return None

        token = query.get("access_token", [""])[0]
        if not token:
            raise ApiError(STATUS_NO_TOKEN, "access_token is missing")
        if not network.token_valid(token):
            raise ApiError(STATUS_TOKEN_EXPIRED, "access_token expired or invalid")

        return handler(match.group(1), self.request_params(method))

    def request_params(self, method: str) -> dict:
        """Decrypt the POST body. GET endpoints used here take no parameters."""
        if method != "POST":
            return {}
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            return json.loads(decrypt(body.decode(), SECRET_KEY))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(STATUS_BAD_PARAMETER, "cannot decrypt request body")

    def reply(self, body: dict) -> None:
        # Errors are reported in the envelope's status, not the HTTP status.
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args) -> None:
        pass  # keep the output down to what the server does, not every request


def device_loop() -> None:
    """Pretend the module wakes up and sends a packet every few seconds."""
    for counter in itertools.count(1):
        network.receive_uplink(f"hello #{counter}".encode())
        time.sleep(UPLINK_INTERVAL_S)


def main() -> None:
    url = urlsplit(BASE_URL)
    server = ThreadingHTTPServer((url.hostname, url.port), Handler)
    print(f"[server] listening on {BASE_URL}")
    threading.Thread(target=device_loop, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] stopping")
        server.server_close()


if __name__ == "__main__":
    main()
