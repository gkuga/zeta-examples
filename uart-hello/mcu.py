"""The user MCU side of the ZETA UART hello example.

This is the code you would keep when you swap the fake module for a real
TZM902DP: point it at the serial port the module is wired to (for example
/dev/ttyAMA0 on a Raspberry Pi) and it speaks the same frames.

    uv run mcu.py [PORT]            # PORT defaults to ./module.tty
    uv run mcu.py --test-mode       # also shorten the heartbeat to 10 s
"""

import argparse
import itertools
import time
from datetime import datetime

import serial

import frame as f

SEND_INTERVAL_S = 10
REPLY_TIMEOUT_S = 3
SEND_RETRIES = 3


class Module:
    """Talks to a TZM902DP over UART.

    A downlink can arrive at any moment, even while we wait for the answer to
    something else, so every read goes through `next_frame`, which prints
    downlinks and hands back everything else.
    """

    def __init__(self, port: str) -> None:
        # 115200 8N1 per the manual; a PTY ignores the speed.
        self.port = serial.Serial(port, 115200, timeout=0.1)

    def wakeup(self) -> None:
        """Wake the module before sending.

        On real hardware, drive the WAKEUP pin low, wait at least 10 ms, send,
        then drive it high again. A PTY has no pins, so there is nothing to do.
        """

    def request(self, frame_type: int, payload: bytes = b"") -> tuple[int, bytes] | None:
        """Send one frame and return the module's answer, or None on timeout."""
        self.wakeup()
        self.port.write(f.encode(frame_type, payload))
        return self.next_frame(time.monotonic() + REPLY_TIMEOUT_S)

    def next_frame(self, deadline: float) -> tuple[int, bytes] | None:
        while time.monotonic() < deadline:
            raw = f.read_frame(self.port.read)
            if not raw:
                continue
            try:
                frame_type, payload = f.decode(raw)
            except f.FrameError as err:
                print(f"[mcu] bad frame from module: {err}")
                continue
            if frame_type == f.DOWNLINK_DATA:
                # On real hardware the INT pin went high 50 ms before this.
                print(f"[mcu] downlink: {payload!r}")
                continue
            return frame_type, payload
        return None

    def listen(self, seconds: float) -> None:
        """Only wait for downlinks."""
        self.next_frame(time.monotonic() + seconds)

    def wait_until_registered(self) -> None:
        last = None
        while True:
            reply = self.request(f.INQUIRE_MODULE_STATUS)
            status = reply[0] if reply else None
            if status != last:
                print(f"[mcu] module status: {f.name(status) if reply else 'no answer'}")
                last = status
            if status == f.STATUS_REGISTERED:
                return
            time.sleep(1)

    def send(self, data: bytes) -> bool:
        """Hand data to the module, retrying as the manual says to."""
        for attempt in range(1, SEND_RETRIES + 1):
            reply = self.request(f.SEND_DATA, data)
            if reply and reply[0] == f.DATA_ACK:
                return True
            answer = f.name(reply[0]) if reply else "no answer"
            print(f"[mcu] send attempt {attempt} failed: {answer}")
            time.sleep(1)
        return False


def show_module_info(module: Module) -> None:
    for frame_type, label, fmt in [
        (f.INQUIRE_VERSION, "version", lambda p: p.hex(" ")),
        (f.INQUIRE_MAC, "MAC", lambda p: p.hex()),
        (f.INQUIRE_NETWORK_TIME, "network time", format_time),
        (f.INQUIRE_NETWORK_QUALITY, "RSSI", lambda p: f"-{p[0]} dBm"),
    ]:
        reply = module.request(frame_type)
        if reply and reply[0] == frame_type:
            print(f"[mcu] {label}: {fmt(reply[1])}")
        else:
            print(f"[mcu] {label}: {f.name(reply[0]) if reply else 'no answer'}")


def format_time(p: bytes) -> str:
    return str(datetime(int.from_bytes(p[:2], "big"), *p[2:7]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("port", nargs="?", default="module.tty")
    parser.add_argument("--test-mode", action="store_true", help="heartbeat every 10 s")
    args = parser.parse_args()

    module = Module(args.port)
    print(f"[mcu] opened {args.port}")

    module.wait_until_registered()
    show_module_info(module)

    if args.test_mode:
        reply = module.request(f.SET_TEST_MODE, b"\x01")
        print(f"[mcu] test mode: {f.name(reply[0]) if reply else 'no answer'}")

    try:
        for counter in itertools.count(1):
            data = f"hello #{counter}".encode()
            if module.send(data):
                print(f"[mcu] handed to module: {data!r}")
            module.listen(SEND_INTERVAL_S)
    except KeyboardInterrupt:
        print("\n[mcu] stopping")


if __name__ == "__main__":
    main()
