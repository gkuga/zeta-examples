"""A fake TZM902DP ZETA module on a pseudo-terminal.

It stands in for everything above the UART -- the module, the radio, the Motes,
the AP and the ZETA Server -- and speaks the module's UART frame protocol on a
PTY. An MCU program opens the PTY like the serial port a real module would be
wired to.

    uv run module_mock.py

Type a line in this terminal to queue it as a downlink "from the server".
"""

import os
import select
import signal
import sys
import threading
import time
import tty
from collections import deque
from datetime import datetime

import frame as f

LINK = "module.tty"  # symlink to the PTY, so the MCU side has a fixed path

# Identity the fake module reports. The MAC is 4 bytes, the same size as the
# 8-hex-digit uid the ZETA Server API uses for a module; hello uses 88880025.
MAC = bytes.fromhex("88880025")
VERSION = bytes([0x00, 0x09, 0x01, 0x02])  # the manual does not explain the bytes
RSSI = 72  # reported as a magnitude: 72 means -72 dBm

REGISTER_DELAY_S = 5  # time to find an AP or Mote after power-on
BUFFER_FRAMES = 2  # uplinks the module holds while the radio is busy
AIR_RATE_BPS = 300  # TZM902DP transmission rate
HEARTBEAT_S = {0x00: 6 * 60 * 60, 0x01: 10}  # Set Test mode: normal / test
DOWNLINK_INT_DELAY_S = 0.05  # INT goes high 50 ms before the frame
IDLE_GAP_S = 0.02  # silence that ends a frame from the MCU


class Module:
    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.write_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.registered = False
        self.heartbeat_s = HEARTBEAT_S[0x00]
        self.uplinks: deque[bytes] = deque()
        self.downlinks: deque[bytes] = deque()
        self.radio_wakeup = threading.Event()

    # -- UART side --------------------------------------------------------

    def write(self, frame_type: int, payload: bytes = b"") -> None:
        with self.write_lock:
            os.write(self.fd, f.encode(frame_type, payload))

    def serve_uart(self) -> None:
        """Read frames from the MCU and answer each one.

        A real module wakes on the WAKEUP pin and stops at the last stop bit.
        A PTY has no pins, so a short silence marks the end of a frame instead.
        That is also what lets it tell a length error from a partial read.
        """
        buffer = b""
        while True:
            timeout = IDLE_GAP_S if buffer else None
            ready, _, _ = select.select([self.fd], [], [], timeout)
            if ready:
                buffer += os.read(self.fd, 256)
                continue
            self.handle(buffer)
            buffer = b""

    def handle(self, raw: bytes) -> None:
        try:
            frame_type, payload = f.decode(raw)
        except f.FrameError as err:
            print(f"[module] rejected: {err}")
            self.write(err.reply)
            return

        if frame_type == f.SEND_DATA:
            self.accept_uplink(payload)
        elif frame_type == f.INQUIRE_VERSION:
            self.write(f.INQUIRE_VERSION, VERSION)
        elif frame_type == f.INQUIRE_MAC:
            self.write(f.INQUIRE_MAC, MAC)
        elif frame_type == f.INQUIRE_MODULE_STATUS:
            self.write(f.STATUS_REGISTERED if self.registered else f.STATUS_UNREGISTERED)
        elif not self.registered and frame_type in (f.INQUIRE_NETWORK_TIME, f.INQUIRE_NETWORK_QUALITY):
            # Both come from the network. The manual does not say what an
            # unregistered module answers; this mock says it is unregistered.
            self.write(f.STATUS_UNREGISTERED)
        elif frame_type == f.INQUIRE_NETWORK_TIME:
            self.write(f.INQUIRE_NETWORK_TIME, network_time())
        elif frame_type == f.INQUIRE_NETWORK_QUALITY:
            self.write(f.INQUIRE_NETWORK_QUALITY, bytes([RSSI]))
        elif frame_type == f.SET_TEST_MODE:
            self.set_test_mode(payload)
        else:
            print(f"[module] ignoring unknown type 0x{frame_type:02X}")

    def accept_uplink(self, payload: bytes) -> None:
        # The ACK means "in my buffer", not "delivered": the radio sends later.
        if not self.registered:
            self.write(f.STATUS_UNREGISTERED)
            return
        if not payload:
            self.write(f.LENGTH_ERROR)
            return
        with self.state_lock:
            full = len(self.uplinks) >= BUFFER_FRAMES
            if not full:
                self.uplinks.append(payload)
        if full:
            print(f"[module] buffer full, dropped {payload!r}")
            self.write(f.BUFFER_FULL)
            return
        self.write(f.DATA_ACK)
        self.radio_wakeup.set()

    def set_test_mode(self, payload: bytes) -> None:
        if len(payload) != 1 or payload[0] not in HEARTBEAT_S:
            self.write(f.SET_FAILED)
            return
        self.heartbeat_s = HEARTBEAT_S[payload[0]]
        print(f"[module] heartbeat interval set to {self.heartbeat_s} s")
        self.write(f.SET_SUCCEED)
        self.radio_wakeup.set()

    # -- radio side -------------------------------------------------------

    def run_radio(self) -> None:
        """Register, then send buffered uplinks and heartbeats over the air."""
        time.sleep(REGISTER_DELAY_S)
        self.registered = True
        print("[module] registered with the network")

        last_heartbeat = time.monotonic()
        while True:
            wait = last_heartbeat + self.heartbeat_s - time.monotonic()
            self.radio_wakeup.wait(timeout=max(wait, 0))
            self.radio_wakeup.clear()

            with self.state_lock:
                payload = self.uplinks.popleft() if self.uplinks else None
            if payload is not None:
                self.transmit(payload, f"uplink {payload!r}")
                self.radio_wakeup.set()  # more may be waiting
            elif time.monotonic() - last_heartbeat >= self.heartbeat_s:
                self.transmit(b"", "heartbeat")
                last_heartbeat = time.monotonic()

    def transmit(self, payload: bytes, what: str) -> None:
        # 300 bps is slow: a 50-byte payload alone is over a second on air.
        time.sleep((len(payload) + 16) * 8 / AIR_RATE_BPS)
        print(f"[module] {what} sent over the air")
        # ACK downlink mode, the default: the module listens only right after
        # it has transmitted, so this is when a queued downlink arrives.
        with self.state_lock:
            downlink = self.downlinks.popleft() if self.downlinks else None
        if downlink is not None:
            time.sleep(DOWNLINK_INT_DELAY_S)
            print(f"[module] downlink to MCU: {downlink!r}")
            self.write(f.DOWNLINK_DATA, downlink)

    def queue_downlink(self, data: bytes) -> None:
        data = data[: f.MAX_PAYLOAD]
        with self.state_lock:
            self.downlinks.append(data)
        print(f"[module] downlink queued, waits for the next transmission: {data!r}")


def network_time() -> bytes:
    """Year (2 bytes, big-endian), month, day, hour, minute, second."""
    now = datetime.now()
    return now.year.to_bytes(2, "big") + bytes(
        [now.month, now.day, now.hour, now.minute, now.second]
    )


def open_pty() -> tuple[int, int, str]:
    master, slave = os.openpty()
    # Raw mode: no echo, no line editing, no CR/LF translation -- bytes only.
    tty.setraw(slave)
    return master, slave, os.ttyname(slave)


def main() -> None:
    # Keep the slave end open ourselves so the PTY survives the MCU program
    # closing and reopening it.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # clean up on kill too
    master, _slave, path = open_pty()
    if os.path.islink(LINK):
        os.remove(LINK)
    os.symlink(path, LINK)
    print(f"[module] listening on {path} (linked as ./{LINK})")

    module = Module(master)
    threading.Thread(target=module.serve_uart, daemon=True).start()
    threading.Thread(target=module.run_radio, daemon=True).start()

    try:
        for line in sys.stdin:
            if line.strip():
                module.queue_downlink(line.strip().encode())
        threading.Event().wait()  # stdin closed: keep serving the PTY
    except KeyboardInterrupt:
        print("\n[module] stopping")
    finally:
        os.remove(LINK)


if __name__ == "__main__":
    main()
