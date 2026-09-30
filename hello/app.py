"""The application side of the ZETA hello example.

This is the code you would keep when you swap the fake server for a real ZETA
Server: it only talks to the RESTful API and never touches the AP, the Motes or
the radio.

    uv run app.py
"""

import json
import time
from datetime import datetime, timedelta

import httpx

from common import (
    API_KEY,
    BASE_URL,
    CMD_DONE,
    SECRET_KEY,
    STATUS_OK,
    STATUS_TOKEN_EXPIRED,
    TOKEN_LIFETIME_S,
    ctl_history_path,
    encrypt,
    module_list_path,
    passthrough_path,
    sign,
    token_path,
    upload_data_path,
)

POLL_INTERVAL_S = 5

# Look back a little on every poll: a record can be stored slightly after the
# time it is stamped with, and a window that starts exactly at the previous
# poll would miss it. Duplicates are dropped by objectid.
POLL_OVERLAP_S = 30


class ZetaError(Exception):
    def __init__(self, status: int, errmsg: str) -> None:
        super().__init__(f"status {status}: {errmsg}")
        self.status = status


class ZetaClient:
    """A minimal client: token handling, encryption and the response envelope."""

    def __init__(self, base_url: str, api_key: str, secret_key: str) -> None:
        self.http = httpx.Client(base_url=base_url, timeout=10)
        self.api_key = api_key
        self.secret_key = secret_key
        self.token = ""
        self.token_expiry = 0.0

    def get(self, path: str) -> list:
        return self.call("GET", path)

    def post(self, path: str, params: dict) -> list:
        # Request parameters travel as encrypted JSON; the response comes back
        # in the clear.
        return self.call("POST", path, encrypt(json.dumps(params), self.secret_key))

    def call(self, method: str, path: str, body: str | None = None) -> list:
        try:
            return self.request(method, path, body)
        except ZetaError as err:
            if err.status != STATUS_TOKEN_EXPIRED:
                raise
            self.token = ""  # expired under us: get a new one and retry once
            return self.request(method, path, body)

    def request(self, method: str, path: str, body: str | None) -> list:
        params = {"access_token": self.access_token()}
        response = self.http.request(method, path, params=params, content=body)
        return self.unwrap(response)

    def access_token(self) -> str:
        # Tokens live 5 minutes; renew a little early.
        if not self.token or time.time() > self.token_expiry - 30:
            params = {"api_key": self.api_key, "signal": sign(self.api_key, self.secret_key)}
            data = self.unwrap(self.http.get(token_path(), params=params))
            self.token = data[0]["access_token"]
            self.token_expiry = time.time() + TOKEN_LIFETIME_S
        return self.token

    @staticmethod
    def unwrap(response: httpx.Response) -> list:
        response.raise_for_status()
        body = response.json()
        if body["status"] != STATUS_OK:
            raise ZetaError(body["status"], body["errmsg"])
        return body["data"]


def same_day_windows(start: int, end: int):
    """Split [start, end] so no piece crosses midnight.

    The query endpoints only accept a start and end on the same day.
    """
    while True:
        midnight = datetime.fromtimestamp(start).replace(hour=0, minute=0, second=0)
        next_day = int((midnight + timedelta(days=1)).timestamp())
        if end < next_day:
            yield start, end
            return
        yield start, next_day - 1
        start = next_day


def query_by_date(client: ZetaClient, path: str, start: int, end: int) -> list:
    records = []
    for window_start, window_end in same_day_windows(start, end):
        records += client.post(path, {"starttime": window_start, "endtime": window_end})
    return records


def say_hello(client: ZetaClient, uid: str) -> str:
    """Queue a downlink for a module. Returns the hex payload that was sent."""
    data_hex = b"hello from the app".hex()
    data = client.post(passthrough_path(uid), {"data": data_hex})
    print(f"[app] downlink queued for {uid} as msgid {data[0]['msgid']}")
    return data_hex


def main() -> None:
    client = ZetaClient(BASE_URL, API_KEY, SECRET_KEY)

    modules = [m["uid"] for m in client.get(module_list_path(API_KEY))]
    print(f"[app] connected to {BASE_URL}, modules: {', '.join(modules)}")

    since = int(time.time())
    # Downlinks we are waiting on, as (uid, hex payload). The history does not
    # carry the msgid the send returned, so match on the payload instead.
    waiting = {(uid, say_hello(client, uid)) for uid in modules}
    seen: dict[str, int] = {}  # uplink objectid -> uptimeStamp

    try:
        while True:
            time.sleep(POLL_INTERVAL_S)
            now = int(time.time())
            start = max(since, now - POLL_OVERLAP_S)
            for uid in modules:
                for uplink in query_by_date(client, upload_data_path(uid), start, now):
                    if uplink["objectid"] in seen:
                        continue
                    seen[uplink["objectid"]] = uplink["uptimeStamp"]
                    data = bytes.fromhex(uplink["updata"])
                    print(f"[app] uplink from {uid} via ap {uplink['apuid']}: {data!r}")

                for command in query_by_date(client, ctl_history_path(uid), since, now):
                    key = (uid, command["downdata"])
                    if key in waiting and command["status"] == CMD_DONE:
                        waiting.discard(key)
                        print(f"[app] downlink delivered to {uid} at {command['uptime']}")

            # Forget uplinks that have left the overlap window.
            seen = {oid: ts for oid, ts in seen.items() if ts >= now - POLL_OVERLAP_S}
    except KeyboardInterrupt:
        print("\n[app] stopping")


if __name__ == "__main__":
    main()
