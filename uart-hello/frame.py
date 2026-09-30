"""The TZM902DP UART frame format, shared by the fake module and the MCU side.

From the TOPPAN "ZETA wireless module TZM902DP user manual" v1.3, section 4:

    +----------+--------+------+-----------+---------+
    | Preamble | Length | Type | Payload   | CRC16   |
    | FA F5    | 1 byte | 1    | 0..50     | 2, BE   |
    +----------+--------+------+-----------+---------+

- Length counts Type + Payload + CRC, so a frame without payload has Length 3.
- The CRC is CRC-16/XMODEM (poly 0x1021, init 0) over Length, Type and
  Payload -- everything except the preamble.
- The link is 115200 bps, 8N1, no flow control.
"""

PREAMBLE = b"\xfa\xf5"
MAX_PAYLOAD = 50

# MCU -> module
SEND_DATA = 0x02  # "Send Variable Length data"; manuals before v1.3 said 0x01
INQUIRE_VERSION = 0x00
INQUIRE_MAC = 0x10
INQUIRE_NETWORK_TIME = 0x11
INQUIRE_NETWORK_QUALITY = 0x13
INQUIRE_MODULE_STATUS = 0x14
SET_TEST_MODE = 0x22

# module -> MCU. Answers to inquiries reuse the inquiry's type.
DATA_ACK = 0x01
BUFFER_FULL = 0x02
LENGTH_ERROR = 0x03
CRC_ERROR = 0x04
SET_SUCCEED = 0x20
SET_FAILED = 0x21
DOWNLINK_DATA = 0x30  # "Wakeup Reason Downlink Data"
STATUS_UNREGISTERED = 0x43
STATUS_REGISTERED = 0x44
STATUS_LONG_SLEEP = 0x45
STATUS_SHORT_SLEEP = 0x46

NAMES = {
    DATA_ACK: "DATA_ACK",
    BUFFER_FULL: "BUFFER_FULL",
    LENGTH_ERROR: "LENGTH_ERROR",
    CRC_ERROR: "CRC_ERROR",
    SET_SUCCEED: "SET_SUCCEED",
    SET_FAILED: "SET_FAILED",
    DOWNLINK_DATA: "DOWNLINK_DATA",
    STATUS_UNREGISTERED: "UNREGISTERED",
    STATUS_REGISTERED: "REGISTERED",
    STATUS_LONG_SLEEP: "LONG_SLEEP",
    STATUS_SHORT_SLEEP: "SHORT_SLEEP",
}


class FrameError(Exception):
    """A frame that cannot be accepted. `reply` is the type to answer with."""

    def __init__(self, reply: int, message: str) -> None:
        super().__init__(message)
        self.reply = reply


def crc16_xmodem(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = (crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


def encode(frame_type: int, payload: bytes = b"") -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload is {len(payload)} bytes, the limit is {MAX_PAYLOAD}")
    body = bytes([3 + len(payload), frame_type]) + payload
    return PREAMBLE + body + crc16_xmodem(body).to_bytes(2, "big")


def decode(frame: bytes) -> tuple[int, bytes]:
    """Check one complete frame and return (type, payload).

    Length is checked before the CRC, which is what the manual's examples
    imply: its length-error example carries a CRC that is correct for the
    bytes actually sent.
    """
    if len(frame) < 6 or not frame.startswith(PREAMBLE):
        raise FrameError(LENGTH_ERROR, f"not a frame: {frame.hex(' ')}")
    length, body = frame[2], frame[2:-2]
    if length != len(frame) - 3:
        raise FrameError(LENGTH_ERROR, f"length says {length}, got {len(frame) - 3}")
    if crc16_xmodem(body) != int.from_bytes(frame[-2:], "big"):
        raise FrameError(CRC_ERROR, f"bad CRC: {frame.hex(' ')}")
    return frame[3], frame[4:-2]


def read_frame(read) -> bytes:
    """Read one frame using `read(n) -> bytes` (e.g. a pyserial port).

    Skips anything before the preamble and trusts the length byte, which is
    how an MCU reads the module's output. Returns b"" on timeout.
    """
    window = b""
    while window != PREAMBLE:
        byte = read(1)
        if not byte:
            return b""
        window = (window + byte)[-2:]
    length = read(1)
    if not length:
        return b""
    rest = read(length[0])
    if len(rest) != length[0]:
        return b""
    return PREAMBLE + length + rest


def name(frame_type: int) -> str:
    return NAMES.get(frame_type, f"0x{frame_type:02X}")
