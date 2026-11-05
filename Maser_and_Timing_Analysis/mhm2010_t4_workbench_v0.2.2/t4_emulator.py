#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Loopback UDP emulator of a T4 MONIT responder, with fault injection.

===============================================================================
 Title          : T4 MONIT Emulator
 Filename       : t4_emulator.py
 Version        : 0.0.2 (Semantic Versioning 2.0.0)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-03
 Modified       : 2026-11-03
 Language       : Python 3.12
 Requires       : numpy; t4_protocol
 Licence        : GPL-3.0-or-later; see LICENSE
 Status         : Test and demonstration tool. Values are SYNTHETIC.
 Revision       : 0.0.1 Nominal synthetic telemetry, slow drifts, lock
                  nibble control and faults (drop, garbage, truncate,
                  wrong peer, delay, duplicate).
                  0.0.2 Nominal codes are the example MONIT reply printed in
                  T4S-MAN-0012 1.27 Sec. 9.13; Type 1 (CR LF) or Type 2 (CR)
                  terminators; answers the documented read-only queries
                  (RDFS, GETID, RDREG=...); any other command is counted as
                  rejected and never answered.
===============================================================================

It answers only the documented read-only queries and counts anything else
as a rejected command, so tests can prove the monitor never sends a setting
command. It binds to 127.0.0.1 by default and never forwards traffic.

The nominal codes are the OEM manual's example reply; noise and a slow
daily ripple are added. They are NOT this site's maser values.

Command line (for a manual GUI demo):

    python t4_emulator.py --port 14000
"""
from __future__ import annotations

from dataclasses import dataclass, field
import argparse
import socket
import threading
import time

import numpy as np

from t4_protocol import (MONIT_COMMAND, READ_ONLY_QUERIES, T4Schema, encode_frame, load_schema,
                         parse_frame)

__version__ = "0.0.2"

#: Example MONIT reply printed in T4S-MAN-0012 issue 1.27, Sec. 9.13.
MANUAL_EXAMPLE = (b"$45E00F47FACC71943D18318ECB30C80C808B1D40E41280730786E40978A000692CB21043B1E"
                  b"03A10D5424D53EF91E818F5C0C08181C9DA7A1")
#: Read-only answers (formats from T4S-MAN-0015 Sec. 1.1.1). The synthesizer
#: value is the web page FREQ field minus 1.42 GHz; others are placeholders.
QUERY_ANSWERS = {b"RDFS;\r\n": b"$RDFS ;=405750.927598720;\r\n",
                 b"GETID;\r\n": b"$GETID;= T4 emulator (synthetic);\r\n",
                 b"RDREG=DRIFTCOMP;\r\n": b"$RDREG=DRIFTCOMP;=+0.0e+00;\r\n",
                 b"RDREG=LSBWEIGHT;\r\n": b"$RDREG=LSBWEIGHT;=1e-17;\r\n",
                 b"RDREG=UVAR;\r\n": b"$RDREG=UVAR;=78.8100;\r\n",
                 b"RDREG=SETH;\r\n": b"$RDREG=SETH;=63.004;\r\n",
                 b"RDREG=ADJ1PPS;\r\n": b"$RDREG=ADJ1PPS;=000000000;\r\n"}


@dataclass
class FaultPlan:
    """Faults applied to the next replies, in this priority order."""
    drop: int = 0              # ignore this many MONIT requests
    garbage: int = 0           # reply with non-hex payloads
    truncate: int = 0          # reply with a short payload
    wrong_peer: int = 0        # reply from a different source port
    duplicate: int = 0         # send each reply twice
    delay_s: float = 0.0       # sleep before every reply


@dataclass
class EmulatorStats:
    requests: int = 0
    replies: int = 0
    queries: int = 0
    rejected_commands: list = field(default_factory=list)


class T4Emulator:
    """Threaded loopback responder. Use as a context manager.

    Parameters
    ----------
    host, port:
        Bind address; port 0 picks a free port (see ``address``).
    schema:
        Channel schema; default is the shipped schema.
    lock_nibble:
        Raw value of channel 41 (main PLL lock: 1 locked, 0 unlocked).
    terminator:
        b"\\r\\n" (Type 1, Card FPGA) or b"\\r" (Type 2, IO card LAN).
    seed:
        Random seed for reproducible noise.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0, schema: T4Schema | None = None,
                 lock_nibble: int = 1, seed: int = 1, terminator: bytes = b"\r\n"):
        self.schema = schema or load_schema()
        self.lock_nibble = lock_nibble
        self.terminator = terminator
        self._nominal = parse_frame(MANUAL_EXAMPLE + b"\r\n", self.schema).codes
        self.faults = FaultPlan()
        self.stats = EmulatorStats()
        self._random = np.random.default_rng(seed)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.bind((host, port))
        self._socket.settimeout(0.1)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="T4Emulator", daemon=True)
        self._start = time.monotonic()

    @property
    def address(self) -> tuple[str, int]:
        return self._socket.getsockname()

    def __enter__(self) -> "T4Emulator":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._socket.close()

    # ---------------------------------------------------------------- frames
    def codes(self) -> list[int]:
        """Synthetic codes: manual example + slow daily ripple + 1-LSB noise."""
        elapsed_h = (time.monotonic() - self._start) / 3600.0
        codes = []
        for channel, nominal in zip(self.schema.channels, self._nominal):
            if channel.is_lock:
                codes.append(self.lock_nibble & 0xF)
            elif channel.available:
                value = nominal * (1 + 0.002 * np.sin(2 * np.pi * elapsed_h / 24)) + self._random.normal(0, 1.0)
                codes.append(int(min(max(round(value), 0), channel.full_scale_code)))
            else:
                codes.append(nominal)
        return codes

    def frame(self) -> bytes:
        return encode_frame(self.codes(), self.schema, terminator=self.terminator)

    # ---------------------------------------------------------------- server
    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                data, peer = self._socket.recvfrom(512)
            except socket.timeout:
                continue
            except OSError:
                return
            if data in QUERY_ANSWERS and data in READ_ONLY_QUERIES:
                self.stats.queries += 1
                self._socket.sendto(QUERY_ANSWERS[data], peer)
                continue
            if data != MONIT_COMMAND:
                self.stats.rejected_commands.append(data)
                continue
            self.stats.requests += 1
            faults = self.faults
            if faults.drop > 0:
                faults.drop -= 1
                continue
            if faults.delay_s:
                time.sleep(faults.delay_s)
            reply = self.frame()
            if faults.garbage > 0:
                faults.garbage -= 1
                reply = b"$" + b"Z" * self.schema.hex_characters + b"\r\n"
            elif faults.truncate > 0:
                faults.truncate -= 1
                reply = reply[:40] + self.terminator
            sender = self._socket
            other = None
            if faults.wrong_peer > 0:
                faults.wrong_peer -= 1
                other = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                other.bind((self.address[0], 0))
                sender = other
            try:
                sender.sendto(reply, peer)
                if faults.duplicate > 0:
                    faults.duplicate -= 1
                    sender.sendto(reply, peer)
                self.stats.replies += 1
            finally:
                if other is not None:
                    other.close()


# %% ===========================================================================
#    COMMAND LINE (MANUAL DEMONSTRATION ONLY)
# =============================================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="Loopback T4 MONIT emulator (synthetic values).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=14000)
    parser.add_argument("--lock-nibble", type=lambda text: int(text, 0), default=1)
    arguments = parser.parse_args()
    with T4Emulator(arguments.host, arguments.port, lock_nibble=arguments.lock_nibble) as emulator:
        print(f"T4 emulator {__version__} on {emulator.address}; Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print(f"Requests {emulator.stats.requests}, rejected {len(emulator.stats.rejected_commands)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
