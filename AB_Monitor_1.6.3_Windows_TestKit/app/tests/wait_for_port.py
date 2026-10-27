#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# =============================================================================
#  wait_for_port.py - wait until TCP ports accept connections (test-kit helper)
#  Version : 0.0.1   Created/Modified: 2026-10-27   Author: W. W. Wallace
#  Used by the Windows kit's 4_ and 5_*.bat instead of a fixed 3 s pause, so
#  the first poll never finds the emulator not yet listening (1.6.2 kit issue).
#  Usage:  python wait_for_port.py --hosts 127.0.0.2,127.0.0.3 --port 18080 [--timeout 30]
#  Exit code 0 when every host answers, 1 after the timeout.
# =============================================================================
from __future__ import annotations

import argparse
import socket
import sys
import time


def wait_for(hosts: list, port: int, timeout: float) -> list:
    """Return the hosts still not accepting connections after ``timeout`` s."""
    deadline = time.monotonic() + timeout
    waiting = list(hosts)
    while waiting and time.monotonic() < deadline:
        for host in list(waiting):
            try:
                socket.create_connection((host, port), timeout=1.0).close()
                waiting.remove(host)            # this host is ready
            except OSError:
                pass                            # not listening yet
        if waiting:
            time.sleep(0.25)
    return waiting


def main() -> int:
    parser = argparse.ArgumentParser(description="Wait until host:port accepts connections.")
    parser.add_argument("--hosts", default="127.0.0.2,127.0.0.3")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]
    missing = wait_for(hosts, args.port, args.timeout)
    if missing:
        print(f" Emulator not answering on {missing} port {args.port} after {args.timeout:.0f} s.")
        return 1
    print(f" Emulator ready on {', '.join(hosts)} port {args.port}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
