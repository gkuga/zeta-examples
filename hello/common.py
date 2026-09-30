"""Shared settings, paths and crypto for the ZETA hello example.

Everything here follows the ZETA Server RESTful API (Techsor "ZETA92JP LPWAN
platform RESTful API specification" v2.11), which is what an application uses
to reach devices on a ZETA network:

    GET  auth_v1/auth_token/query/getWanAccessToken?api_key=..&signal=..
    GET  zeta_v1/wan_ms/query/{api_key}/getMsList
    POST zeta_v1/wan_ms/query/{uid}/getMsUploadDataByDate
    POST zeta_v1/wan_ms/control/{uid}/newCtrlMsPassthrough
    POST zeta_v1/wan_ms/query/{uid}/getMsCtlHistoryByDate

"ms" is the API's name for a module, i.e. an end device. Every response has
the same envelope:

    {"data": [...], "errmsg": "", "status": 0, "ts": <unix seconds>}
"""

import base64
import hashlib
import hmac
import os
import secrets

from cryptography.hazmat.decrepit.ciphers.modes import CFB8
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

# Every path hangs off this prefix. The hosted ZETA Cloud Platform uses
# https://www.zetacloud.com:25455/teamcms/ws/ instead.
BASE_URL = os.environ.get("ZETA_BASE_URL", "http://localhost:8080/teamcms/ws/")

# A real account finds both in the ZETA management console. These defaults are
# the sample values printed in the specification, which makes the signature
# and ciphertext below checkable against the document.
API_KEY = os.environ.get("ZETA_API_KEY", "d303e92eae5543fa8a48af4814d5244a")
SECRET_KEY = os.environ.get("ZETA_SECRET_KEY", "4e0e0947ae8f435a96d398b715c5f078")

# Identifiers the fake server pretends to have. Every device -- module, Mote
# (relay) and AP (base station) -- is named by an 8-character uid.
MODULE_UID = "88880025"
MOTE_UID = "E0000011"
AP_UID = "D0000011"
AP_HOST_UID = "F0000011"  # "msguid": the host the AP belongs to

TOKEN_LIFETIME_S = 5 * 60

# Return codes shared by every endpoint.
STATUS_OK = 0
STATUS_SERVER_ERROR = -1
STATUS_AUTH_FAILED = 10000
STATUS_BAD_PARAMETER = 10001
STATUS_NO_TOKEN = 10002
STATUS_TOKEN_EXPIRED = 10003

# Downlink command type 8 is "transparent transmission": opaque bytes for the
# user MCU behind the module.
CMD_TYPE_PASSTHROUGH = 8
# Execution states in the downlink history.
CMD_PENDING = 300
CMD_DONE = 301


def token_path() -> str:
    return "auth_v1/auth_token/query/getWanAccessToken"


def module_list_path(api_key: str) -> str:
    return f"zeta_v1/wan_ms/query/{api_key}/getMsList"


def upload_data_path(uid: str) -> str:
    return f"zeta_v1/wan_ms/query/{uid}/getMsUploadDataByDate"


def passthrough_path(uid: str) -> str:
    return f"zeta_v1/wan_ms/control/{uid}/newCtrlMsPassthrough"


def ctl_history_path(uid: str) -> str:
    return f"zeta_v1/wan_ms/query/{uid}/getMsCtlHistoryByDate"


def sign(api_key: str, secret_key: str) -> str:
    """The `signal` parameter of the token request: HMAC-SHA1 of the api_key."""
    return hmac.new(secret_key.encode(), api_key.encode(), hashlib.sha1).hexdigest()


def encrypt(plaintext: str, secret_key: str, iv: str | None = None) -> str:
    """Encrypt request parameters the way the API expects.

    AES/CFB8/NoPadding keyed with the secret_key, then base64 of IV + ciphertext.
    The IV is a random 16-character *string*, sent in the clear up front so the
    server can decrypt. Only requests are encrypted; responses are plain JSON.
    """
    if iv is None:
        iv = secrets.token_hex(8)
    encryptor = Cipher(algorithms.AES(secret_key.encode()), CFB8(iv.encode())).encryptor()
    ciphertext = encryptor.update(plaintext.encode()) + encryptor.finalize()
    return base64.b64encode(iv.encode() + ciphertext).decode()


def decrypt(token: str, secret_key: str) -> str:
    raw = base64.b64decode(token)
    iv, ciphertext = raw[:16], raw[16:]
    decryptor = Cipher(algorithms.AES(secret_key.encode()), CFB8(iv)).decryptor()
    return (decryptor.update(ciphertext) + decryptor.finalize()).decode()
