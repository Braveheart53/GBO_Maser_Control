#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Read-only T4 MONIT poller and raw-first recorder with bounded RAM.

===============================================================================
 Title          : T4 Read-Only Monitor and Recorder
 Filename       : t4_monitor.py
 Version        : 0.0.3 (Semantic Versioning 2.0.0)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-03
 Modified       : 2026-11-05
 Language       : Python 3.12
 Requires       : numpy; t4_protocol (psutil optional, for RAM reporting)
 Licence        : GPL-3.0-or-later; see LICENSE
 Status         : Tested only against the loopback emulator, not yet a real
                  T4. Sends only the documented READ-ONLY queries
                  (MONIT, RDFS, GETID, RDREG=...) of T4S-MAN-0015.
 Revision       : 0.0.1 One socket per attempt, bounded retries with backoff,
                  peer validation, stale-datagram drain, cancellation, raw
                  frames persisted before decoding, timestamped session
                  folder, preview ring buffers with RAM flush and pickle
                  offload.
                  0.0.2 Read-only state queries (query(): RDFS, GETID,
                  RDREG=DRIFTCOMP/LSBWEIGHT/UVAR/SETH/ADJ1PPS) through the
                  same validated exchange; UDP port 14000 is the documented
                  default (T4S-MAN-0015 Sec. 1.1.2).
                  0.0.3 Command line: local UDP port 14000 by default for a
                  real T4 (the vendor example and T4_Maser_Comms.py bind it;
                  "seems one must bind the port to local port as well"),
                  ephemeral for --emulator; --local-port 0 = ephemeral.
===============================================================================

SAFETY RULES (from the T4 extension plan, 0.1.0)
-----------------------------------------------
* ALLOWED_COMMANDS holds only documented read-only queries. No setting
  command (SETFSREL, DRIFTCOMP, UVAR=..., SETH=..., ADJ1PPS=..., SYNCPPS,
  REBOOT, ...) can be sent by this program; tests assert this.
* A non-loopback endpoint is refused unless ``read_only_approved`` is set
  by the operator for that endpoint. No endpoint is embedded in the code.
* Receive timestamps come from this computer's clock (UTC); they are NOT
  precision maser timestamps. Round-trip time uses the monotonic clock.
* Every datagram that is not from the polled host and port is counted and
  ignored. Datagrams left over from a previous attempt are drained first.
* Raw replies are written to disk BEFORE decoding, so a later, verified
  schema can re-decode the whole history (maser_io.load_t4_telemetry).

Command line (loopback emulator demo; a real endpoint needs the approval
flag and is the operator's decision):

    python t4_monitor.py --emulator --count 5 --interval 1 --output OUT
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import argparse
import csv
import errno
import gc
import ipaddress
import json
import pickle
import select
import socket
import threading
import time

import numpy as np

from t4_protocol import (MONIT_COMMAND, READ_ONLY_QUERIES, T4Frame, T4FrameError, T4ReplyError,
                         T4Schema, column_names, load_schema, parse_frame, parse_query_reply)

__version__ = "0.0.3"

#: Commands this program may send: documented read-only queries only.
ALLOWED_COMMANDS = frozenset(READ_ONLY_QUERIES)
UNIX_EPOCH_MJD = 40587.0


def utc_now() -> tuple[str, float]:
    """(ISO-8601 UTC text, MJD UTC) from the host clock."""
    now = time.time()
    iso = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="microseconds")
    return iso, now / 86400.0 + UNIX_EPOCH_MJD


def folder_stamp(when: datetime | None = None) -> str:
    """Local time with UTC offset and no underscores, e.g. 20261103T120000-0500."""
    return (when or datetime.now().astimezone()).strftime("%Y%m%dT%H%M%S%z")


# %% ===========================================================================
#    CONFIGURATION AND RESULT RECORDS
# =============================================================================
@dataclass
class MonitorConfig:
    """Endpoint and timing. ``local_port`` None uses an ephemeral port.

    The vendor example and T4_Maser_Comms.py bind local port 14000, and the
    latter notes "seems one must bind the port to local port as well": the
    T4 probably answers to port 14000, not to the sender's port (not stated
    in T4S-MAN-0015). Use local_port=14000 for a real T4; the GUI and the
    command line do so by default (0.0.3). One local port serves ONE monitor
    (serialized requests).
    """
    host: str
    port: int = 14000
    local_port: int | None = None
    timeout_s: float = 1.0
    attempts: int = 3
    backoff: float = 1.5
    interval_s: float = 10.0
    allow_prefix: bool = False
    read_only_approved: bool = False

    def validate(self) -> None:
        if not 0 < int(self.port) < 65536:
            raise ValueError("port must be 1..65535")
        if self.local_port is not None and not 0 <= int(self.local_port) < 65536:
            raise ValueError("local port must be 0..65535")
        if not (0.05 <= self.timeout_s <= 60 and 1 <= self.attempts <= 10 and 1.0 <= self.backoff <= 4.0):
            raise ValueError("timeout 0.05-60 s, attempts 1-10, backoff 1-4")
        if self.interval_s < 0.2:
            raise ValueError("poll interval must be at least 0.2 s")


@dataclass
class PollRecord:
    """Everything known about one poll, successful or not."""
    sequence: int
    request_utc: str
    request_mjd: float
    status: str = "PENDING"
    receive_utc: str = ""
    receive_mjd: float = float("nan")
    rtt_ms: float = float("nan")
    attempt: int = 0
    peer: str = ""
    raw: bytes | None = None
    frame: T4Frame | None = None
    foreign_datagrams: int = 0
    stale_datagrams: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "OK"


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(socket.gethostbyname(host)).is_loopback
    except (OSError, ValueError):
        return False


# %% ===========================================================================
#    POLLER
# =============================================================================
class T4Monitor:
    """Serialized MONIT poller. Thread-safe for one caller at a time."""

    def __init__(self, config: MonitorConfig, schema: T4Schema | None = None):
        config.validate()
        if not is_loopback(config.host) and not config.read_only_approved:
            raise PermissionError("Polling a non-loopback T4 endpoint requires explicit operator "
                                  "approval of read-only MONIT access for that endpoint.")
        self.config = config
        self.schema = schema or load_schema()
        self.remote = (socket.gethostbyname(config.host), int(config.port))
        self.sequence = 0
        self._lock = threading.Lock()
        self.sent_commands: list[bytes] = []          # audit trail for tests

    def _send(self, sock: socket.socket, payload: bytes) -> None:
        if payload not in ALLOWED_COMMANDS:            # defence in depth
            raise PermissionError(f"refusing to send non-monitoring command {payload!r}")
        sock.sendto(payload, self.remote)
        self.sent_commands.append(payload)

    @staticmethod
    def _drain(sock: socket.socket) -> int:
        sock.setblocking(False)
        count = 0
        while True:
            try:
                sock.recvfrom(4096)
                count += 1
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break
        sock.setblocking(True)
        return count

    def poll_once(self, cancel: threading.Event | None = None) -> PollRecord:
        """One MONIT request with bounded retries; never raises for I/O."""
        return self._exchange(MONIT_COMMAND, cancel)

    def query(self, command: bytes, cancel: threading.Event | None = None) -> tuple[PollRecord, str | None]:
        """One documented read-only query, e.g. b"RDFS;\\r\\n" -> (record, answer)."""
        if command == MONIT_COMMAND or command not in ALLOWED_COMMANDS:
            raise PermissionError(f"{command!r} is not an allowed read-only query")
        record = self._exchange(command, cancel)
        if record.status != "RAW_OK":
            return record, None
        try:
            answer = parse_query_reply(record.raw, command)
            record.status = "OK"
            return record, answer
        except T4ReplyError as error:
            record.status, record.error = "REPLY_ERROR", str(error)
            return record, None

    def _exchange(self, command: bytes, cancel: threading.Event | None) -> PollRecord:
        with self._lock:
            self.sequence += 1
            iso, mjd = utc_now()
            record = PollRecord(self.sequence, iso, mjd)
            for attempt in range(1, self.config.attempts + 1):
                if cancel is not None and cancel.is_set():
                    record.status = "CANCELLED"
                    return record
                record.attempt = attempt
                outcome = self._attempt(record, attempt, cancel, command)
                if outcome != "TIMEOUT":
                    return record
            record.status = "TIMEOUT"
            record.error = (f"no reply from {self.remote[0]}:{self.remote[1]} after "
                            f"{self.config.attempts} attempt(s)" + (f"; {record.error}" if record.error else ""))
            return record

    def _attempt(self, record: PollRecord, attempt: int, cancel: threading.Event | None,
                 command: bytes = MONIT_COMMAND) -> str:
        timeout = self.config.timeout_s * self.config.backoff ** (attempt - 1)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except OSError as error:
            record.status, record.error = "SOCKET_ERROR", str(error)
            return record.status
        try:
            if hasattr(socket, "SIO_UDP_CONNRESET"):
                # Windows: an ICMP "port unreachable" would otherwise make the
                # next recvfrom() raise WSAECONNRESET on this UDP socket.
                sock.ioctl(socket.SIO_UDP_CONNRESET, False)
            try:
                sock.bind(("", int(self.config.local_port or 0)))
            except OSError as error:
                record.status = "PORT_IN_USE" if error.errno in (errno.EADDRINUSE, 10048) else "SOCKET_ERROR"
                record.error = f"bind local port {self.config.local_port}: {error}"
                return record.status
            record.stale_datagrams += self._drain(sock)
            start = time.monotonic()
            self._send(sock, command)
            deadline = start + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return "TIMEOUT"
                if cancel is not None and cancel.is_set():
                    record.status = "CANCELLED"
                    return record.status
                readable, _, _ = select.select([sock], [], [], min(0.1, remaining))
                if not readable:
                    continue
                try:
                    data, peer = sock.recvfrom(4096)
                except ConnectionResetError:
                    record.error = "ICMP port unreachable reported for the T4 endpoint"
                    return "TIMEOUT"                     # retry, then report TIMEOUT
                if (peer[0], peer[1]) != self.remote:
                    record.foreign_datagrams += 1
                    continue
                record.rtt_ms = (time.monotonic() - start) * 1e3
                record.receive_utc, record.receive_mjd = utc_now()
                record.peer = f"{peer[0]}:{peer[1]}"
                record.raw = bytes(data)
                if command != MONIT_COMMAND:
                    record.status = "RAW_OK"
                    return record.status
                try:
                    record.frame = parse_frame(record.raw, self.schema,
                                               allow_prefix=self.config.allow_prefix)
                    record.status = "OK"
                except T4FrameError as error:
                    record.status, record.error = f"FRAME_ERROR:{error.reason}", str(error)
                return record.status
        except OSError as error:
            record.status, record.error = "SOCKET_ERROR", str(error)
            return record.status
        finally:
            sock.close()


# %% ===========================================================================
#    RECORDER: RAW FIRST, THEN VALUES; BOUNDED PREVIEW IN RAM
# =============================================================================
RAW_FIELDS = ["sequence", "request_utc", "request_mjd", "receive_utc", "receive_mjd", "rtt_ms",
              "attempt", "peer", "status", "foreign_datagrams", "stale_datagrams", "error", "raw_ascii"]


def escape_raw(raw: bytes | None) -> str:
    """Printable, reversible text for a datagram (\\r, \\n, \\xNN escaped)."""
    if raw is None:
        return ""
    return raw.decode("ascii", "backslashreplace").encode("unicode_escape").decode("ascii")


def unescape_raw(text: str) -> bytes:
    return text.encode("ascii").decode("unicode_escape").encode("latin-1")


class T4Recorder:
    """Append-only session files plus a preview ring buffer for plotting.

    Files in ``<parent>/T4_telemetry_<stamp>/`` (stamp added once, when the
    folder is created):
      t4_raw_frames.csv   every poll, raw datagram escaped (source of truth)
      t4_telemetry.csv    successful polls: values then raw codes
      t4_session.json     schema version/hash, program versions, endpoint
    """

    def __init__(self, parent: str | Path, schema: T4Schema, config: MonitorConfig | None = None,
                 preview_length: int = 20000, fsync_every: int = 10):
        self.schema = schema
        self.folder = Path(parent) / f"T4_telemetry_{folder_stamp()}"
        self.folder.mkdir(parents=True, exist_ok=False)
        self.raw_path = self.folder / "t4_raw_frames.csv"
        self.values_path = self.folder / "t4_telemetry.csv"
        self.preview_length = int(preview_length)
        self.fsync_every = max(1, int(fsync_every))
        self.preview_mjd: deque = deque(maxlen=self.preview_length)
        self.preview_values: deque = deque(maxlen=self.preview_length)
        self.counts = {"polls": 0, "ok": 0, "bad": 0, "write_errors": 0, "offloads": 0}
        self.last_error = ""
        names = column_names(schema)
        self._value_header = ["receive_mjd", "receive_utc", "rtt_ms"] + names + \
            [f"{channel.short_name}_code" for channel in schema.channels]
        for path, header in ((self.raw_path, RAW_FIELDS), (self.values_path, self._value_header)):
            with path.open("w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerow(header)
        session = {"program": "MHM2010/T4 Workbench t4_monitor", "version": __version__,
                   "schema_version": schema.version, "schema_sha256": schema.sha256,
                   "schema_status": schema.status, "created_local": self.folder.name[13:],
                   "clock": "host UTC receive time; not a maser timestamp",
                   "endpoint": None if config is None else {"host": config.host, "port": config.port,
                                                            "local_port": config.local_port},
                   "read_only_command": MONIT_COMMAND.decode("ascii").encode("unicode_escape").decode()}
        (self.folder / "t4_session.json").write_text(json.dumps(session, indent=2), encoding="utf-8")

    def _append(self, path: Path, row: list) -> None:
        with path.open("a", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(row)
            if self.counts["polls"] % self.fsync_every == 0:
                handle.flush()
                try:
                    import os
                    os.fsync(handle.fileno())
                except OSError:
                    pass

    def write(self, record: PollRecord) -> bool:
        """Persist one poll. Returns False (and keeps running) on disk errors."""
        self.counts["polls"] += 1
        self.counts["ok" if record.ok else "bad"] += 1
        try:
            self._append(self.raw_path, [record.sequence, record.request_utc, f"{record.request_mjd:.10f}",
                                         record.receive_utc, f"{record.receive_mjd:.10f}",
                                         f"{record.rtt_ms:.3f}", record.attempt, record.peer, record.status,
                                         record.foreign_datagrams, record.stale_datagrams, record.error,
                                         escape_raw(record.raw)])
            if record.ok and record.frame is not None:
                values = ["" if value is None else f"{value:.6g}" for value in record.frame.values]
                self._append(self.values_path, [f"{record.receive_mjd:.10f}", record.receive_utc,
                                                f"{record.rtt_ms:.3f}", *values, *record.frame.codes])
        except OSError as error:
            self.counts["write_errors"] += 1
            self.last_error = f"write failed ({error}); data kept in RAM preview only"
            return False
        finally:
            if record.ok and record.frame is not None:
                self.preview_mjd.append(record.receive_mjd)
                self.preview_values.append(np.array([np.nan if v is None else v for v in record.frame.values]))
        return True

    def preview_arrays(self) -> tuple[np.ndarray, np.ndarray]:
        """(mjd [N], values [N, channels]) of the in-RAM preview."""
        if not self.preview_mjd:
            return np.empty(0), np.empty((0, len(self.schema.channels)))
        return np.fromiter(self.preview_mjd, float), np.vstack(self.preview_values)

    def preview_bytes(self) -> int:
        return len(self.preview_mjd) * (8 + 8 * len(self.schema.channels) + 112)

    def flush_ram(self) -> int:
        """Drop the preview (all data are on disk); return approx. bytes freed."""
        freed = self.preview_bytes()
        self.preview_mjd.clear()
        self.preview_values.clear()
        gc.collect()
        return freed

    def offload_pickle(self) -> Path | None:
        """Write the preview to a pickle beside the CSVs, then flush RAM."""
        if not self.preview_mjd:
            return None
        mjd, values = self.preview_arrays()
        self.counts["offloads"] += 1
        path = self.folder / f"t4_preview_offload_{self.counts['offloads']:04d}.pkl"
        with path.open("wb") as handle:
            pickle.dump({"mjd": mjd, "values": values, "columns": column_names(self.schema),
                         "schema_sha256": self.schema.sha256}, handle, protocol=pickle.HIGHEST_PROTOCOL)
        self.flush_ram()
        return path


def process_rss_bytes() -> int | None:
    """Resident memory of this process, when psutil is installed."""
    try:
        import psutil
        return int(psutil.Process().memory_info().rss)
    except Exception:                                   # noqa: BLE001
        return None


# %% ===========================================================================
#    POLLING LOOP (USED BY THE GUI WORKER AND THE COMMAND LINE)
# =============================================================================
def run_loop(monitor: T4Monitor, recorder: T4Recorder | None, cancel: threading.Event,
             count: int | None = None, on_record=None) -> int:
    """Poll at the configured interval until cancelled or ``count`` polls."""
    done = 0
    next_time = time.monotonic()
    while not cancel.is_set() and (count is None or done < count):
        record = monitor.poll_once(cancel)
        if record.status == "CANCELLED":
            break
        if recorder is not None:
            recorder.write(record)
        if on_record is not None:
            on_record(record)
        done += 1
        next_time += monitor.config.interval_s
        cancel.wait(max(0.0, next_time - time.monotonic()))
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only T4 MONIT recorder (schema per T4S-MAN-0012 Table 6).")
    parser.add_argument("--host", help="T4 address (operator supplied; none is built in)")
    parser.add_argument("--port", type=int, default=14000)
    parser.add_argument("--local-port", type=int,
                        help="local UDP port; default 14000 for a real T4, ephemeral for --emulator; 0 = ephemeral")
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--count", type=int, help="stop after this many polls")
    parser.add_argument("--output", type=Path, required=True, help="parent folder for the session folder")
    parser.add_argument("--allow-prefix", action="store_true", help="accept text before '$'")
    parser.add_argument("--approve-read-only", action="store_true",
                        help="operator approves read-only MONIT polling of a non-loopback host")
    parser.add_argument("--emulator", action="store_true", help="start and poll the loopback emulator")
    arguments = parser.parse_args()
    emulator = None
    if arguments.emulator:
        from t4_emulator import T4Emulator
        emulator = T4Emulator()
        emulator.start()
        arguments.host, arguments.port = emulator.address
    if not arguments.host:
        parser.error("--host or --emulator is required")
    local_port = arguments.local_port
    if local_port is None:
        local_port = None if arguments.emulator else 14000
    config = MonitorConfig(arguments.host, arguments.port, local_port or None, arguments.timeout,
                           arguments.attempts, 1.5, arguments.interval, arguments.allow_prefix,
                           arguments.approve_read_only)
    monitor = T4Monitor(config)
    recorder = T4Recorder(arguments.output, monitor.schema, config)
    cancel = threading.Event()

    def report(record: PollRecord) -> None:
        print(f"{record.sequence:6d} {record.request_utc} {record.status:24s} "
              f"rtt {record.rtt_ms:8.2f} ms  attempt {record.attempt}")
    try:
        run_loop(monitor, recorder, cancel, arguments.count, report)
    except KeyboardInterrupt:
        cancel.set()
    finally:
        if emulator is not None:
            emulator.stop()
    print(f"Session folder: {recorder.folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
