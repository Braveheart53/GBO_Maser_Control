#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# %% ===========================================================================
# Title: Loopback tests for the operator script tools/T4_Reboot.py | Version: 0.0.1
# Author: William W. Wallace | Created/Modified: 2026-11-05 | Python: 3.12
# Revision: 0.0.1 Fake T4 LAN module on 127.0.0.2 answering in the formats of
#           T4S-MAN-0015 Sec. 1.1.2 / Table 1 (REBOOT: no answer; rejected
#           command: "!<command>"), answering either to fixed port P (as the
#           vendor example implies) or to the source port.
# =============================================================================
"""The script is never pointed at a real maser here: loopback only."""
import contextlib
import importlib.util
import io
import socket
import threading
import time
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "T4_Reboot.py"
MASER_IP, CLIENT_IP, STRAY_IP = "127.0.0.2", "127.0.0.1", "127.0.0.3"
ANSWERS = {"GETID": "$GETID;= Monitoring NDCU Ver. 2.0; \r\n", "RDFS": "$RDFS ;=405750.927598720;\r\n",
           "RDREG=DRIFTCOMP": "$RDREG=DRIFTCOMP;=0;\r\n"}
PRE = ["GETID", "RDFS", "RDREG=DRIFTCOMP"]
FULL = PRE + ["REBOOT"] + PRE


def free_port() -> int:
    """A UDP port free on both loopback addresses."""
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind((CLIENT_IP, 0))
            port = probe.getsockname()[1]
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as other:
                    other.bind((MASER_IP, port))
                return port
            except OSError:
                continue
    raise RuntimeError("no free port")


def loopback_aliases_work() -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind((MASER_IP, 0))
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not loopback_aliases_work(), reason="127.0.0.2 not bindable (e.g. macOS)")


class FakeMaser(threading.Thread):
    def __init__(self, port, mode="fixed", silent=False, reject=(), stray=False, stale=False):
        super().__init__(daemon=True)
        self.port, self.mode, self.silent, self.reject = port, mode, silent, set(reject)
        self.stray, self.stale, self.received, self.running = stray, stale, [], True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((MASER_IP, port))
        self.sock.settimeout(0.05)

    def send(self, text, addr):
        self.sock.sendto(text.encode("ascii"), (addr[0], self.port) if self.mode == "fixed" else addr)

    def run(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            key = data.decode("ascii").split(";")[0].replace(" ", "")
            self.received.append(key)
            if self.silent:
                continue
            if key in self.reject:
                self.send(f"!{key};\r\n", addr)
                continue
            if key == "REBOOT":
                continue
            if self.stray:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as other:
                    other.bind((STRAY_IP, 0))
                    other.sendto(b"$GETID;= STRAY;\r\n",
                                 (addr[0], self.port) if self.mode == "fixed" else addr)
            if self.stale:
                self.send("$RDFS ;=1.0;\r\n" if key != "RDFS" else "$GETID;= OLD;\r\n", addr)
            self.send(ANSWERS[key], addr)

    def close(self):
        self.running = False
        self.join(1)
        self.sock.close()


def load(port, local_port=None, typed="REBOOT"):
    spec = importlib.util.spec_from_file_location("t4_reboot_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.SERVER_ADDRESS, module.SERVER_PORT = MASER_IP, port
    module.LOCAL_ADDRESS = CLIENT_IP
    module.LOCAL_PORT = port if local_port is None else local_port
    module.TIMEOUT_S, module.REBOOT_WAIT_S = 0.4, 0.1
    module.input = lambda prompt: typed
    return module


def run(fake_kwargs=None, local_port=None, typed="REBOOT", blocker=False, patch=None):
    port = free_port()
    fake = FakeMaser(port, **(fake_kwargs or {}))
    fake.start()
    block = None
    if blocker:
        block = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        block.bind((CLIENT_IP, port))
    module = load(port, local_port, typed)
    if patch:
        patch(module)
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            code = module.main()
    finally:
        time.sleep(0.05)
        fake.close()
        if block:
            block.close()
    return code, fake.received, output.getvalue()


def test_header_version():
    assert "0.0.4: Corrections after verification" in SCRIPT.read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", ["fixed", "source"])
def test_full_run(mode):
    code, received, text = run({"mode": mode})
    assert code == 0 and received == FULL
    assert "no answer (expected" in text and "unchanged" in text


def test_ephemeral_port_fails_safe_against_fixed_port_maser():
    code, received, text = run({}, local_port=0)
    assert code == 1 and received == ["GETID"] and "REBOOT was not sent" in text


def test_ephemeral_port_works_against_source_port_maser():
    assert run({"mode": "source"}, local_port=0)[:2] == (0, FULL)


@pytest.mark.parametrize("typed", ["", "reboot", "yes"])
def test_cancel(typed):
    code, received, text = run({}, typed=typed)
    assert code == 0 and received == PRE and "not sent" in text


def test_silent_maser():
    code, received, _ = run({"silent": True})
    assert code == 1 and received == ["GETID"]


def test_rejected_getid_and_reboot():
    assert run({"reject": ["GETID"]})[:2] == (1, ["GETID"])
    code, received, text = run({"reject": ["REBOOT"]})
    assert code == 3 and received == PRE + ["REBOOT"] and "did not reboot" in text


def test_local_port_busy():
    code, received, text = run({}, blocker=True)
    assert code == 1 and received == [] and "cannot bind local UDP port" in text


@pytest.mark.parametrize("kwargs", [{"stray": True}, {"stale": True}])
def test_stray_and_late_answers_ignored(kwargs):
    assert run(kwargs)[:2] == (0, FULL)


def test_connection_reset_is_tolerated():
    def reset_once(module):
        real = socket

        class ResetOnce(real.socket):
            fired = [False]

            def recvfrom(self, size):
                if not ResetOnce.fired[0]:
                    ResetOnce.fired[0] = True
                    raise ConnectionResetError(10054, "connection reset")
                return super().recvfrom(size)
        fake = types.ModuleType("socket")
        fake.__dict__.update(real.__dict__)
        fake.socket = ResetOnce
        module.socket = fake
    assert run({}, patch=reset_once)[:2] == (0, FULL)


def test_allowlist_refuses_writes():
    module = load(free_port())
    for command in ("REBOOT;\r\n", "SETFSREL=+1E-14;\r\n", "DRIFTCOMP=+0e+00;\r\n", "RECMONIT=60;\r\n",
                    "GETID; \r\n"):
        with pytest.raises(PermissionError):
            module.send_read_only(command)
    assert [module.command_key(c) for c in module.READ_ONLY_QUERIES] == ["GETID", "RDFS", "RDREG=DRIFTCOMP"]
