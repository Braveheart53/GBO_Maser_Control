#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# %% ===========================================================================
# Title: T4 protocol, monitor, emulator and settings tests | Version: 0.0.1
# Author: William W. Wallace | Created/Modified: 2026-11-03 | Python: 3.12
# Revision: 0.0.1 Framing, scaling, schema, query replies, UDP fault cases,
#           recorder/RAM, re-decoding and the SETFSREL/DRIFTCOMP model
#           (T4 extension plan 0.1.0 test families).
# =============================================================================
from pathlib import Path
import socket
import sys
import threading
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pytest
from t4_protocol import (MONIT_COMMAND, READ_ONLY_QUERIES, T4FrameError, T4ReplyError, T4SchemaError,
                         decode_many, encode_frame, load_schema, parse_frame, parse_query_reply,
                         schema_from_dict, split_frame, synth_hz_from_reply, values_from_codes)
from t4_emulator import MANUAL_EXAMPLE, T4Emulator
from t4_monitor import MonitorConfig, T4Monitor, T4Recorder, run_loop, unescape_raw, escape_raw
from maser_io import TimingSeries, load_t4_telemetry
from maser_core import fit_phase, t4_settings, T4_SYNTH_STEP_HZ, T4_SYNTH_SCALE_HZ

SCHEMA = load_schema()
VALID = MANUAL_EXAMPLE + b"\r\n"


# %% FRAMING (T4S-MAN-0012 1.27 Sec. 9.13 example replies)
def test_manual_examples_type1_and_type2():
    for end in (b"\r\n", b"\r"):
        frame = parse_frame(MANUAL_EXAMPLE + end, SCHEMA)
        assert frame.terminator == end
        assert frame.locked is True and frame.flags == ()
    values = parse_frame(VALID, SCHEMA).values
    # Engineering values of the manual's example (Table 6 gains).
    assert values[0] == pytest.approx(1118 * 0.02441)          # U batt.A 27.29 V
    assert values[32] == pytest.approx(245 * 0.09766)          # +24 V rail 23.93 V
    assert values[34] == pytest.approx(-192 * 0.07813)         # -15 V rail, negative gain
    assert values[39] == pytest.approx(122 * 0.03906)          # LOCK 100 MHz 4.765 V (web page: 4.764)
    assert values[20] is None and values[36] is None and values[38] is None   # "Not used"


@pytest.mark.parametrize("data, reason", [
    (b"", "EMPTY"),
    (MANUAL_EXAMPLE[:60] + b"\r\n", "TRUNCATED"),
    (MANUAL_EXAMPLE + b"A\r\n", "OVERSIZED"),
    (MANUAL_EXAMPLE[1:] + b"\r\n", "NO_PREFIX"),
    (b"$" + b"G" + MANUAL_EXAMPLE[2:] + b"\r\n", "BAD_HEX"),
    (MANUAL_EXAMPLE + b";\r\n", "EXTRA_BYTES"),
    (MANUAL_EXAMPLE + b"\n", "BAD_TERMINATOR"),
    (MANUAL_EXAMPLE, "NO_TERMINATOR"),
    (b"MONIT" + VALID, "UNEXPECTED_PREFIX"),
    (b"$\xff" + MANUAL_EXAMPLE[2:] + b"\r\n", "NON_ASCII"),
])
def test_malformed_frames(data, reason):
    with pytest.raises(T4FrameError) as caught:
        parse_frame(data, SCHEMA)
    assert caught.value.reason == reason


def test_str_is_refused_and_prefix_is_opt_in():
    with pytest.raises(T4FrameError) as caught:
        parse_frame(VALID.decode(), SCHEMA)
    assert caught.value.reason == "NOT_BYTES"
    frame = parse_frame(b"MONIT" + VALID, SCHEMA, allow_prefix=True)
    assert frame.prefix_bytes == b"MONIT"


# %% SCALING AND SCHEMA
def test_zero_and_full_scale_codes():
    zero = parse_frame(encode_frame([0] * 41, SCHEMA), SCHEMA)
    assert all(value in (0.0, None) for value in zero.values)
    assert zero.locked is False
    full = [c.full_scale_code if not c.is_lock else 1 for c in SCHEMA.channels]
    frame = parse_frame(encode_frame(full, SCHEMA), SCHEMA)
    assert frame.codes[0] == 4095 and frame.codes[32] == 255
    assert frame.values[0] == pytest.approx(4095 * 0.02441)


def test_invalid_lock_code_is_flagged_not_interpreted():
    codes = list(parse_frame(VALID, SCHEMA).codes)
    codes[40] = 0xA
    frame = parse_frame(encode_frame(codes, SCHEMA), SCHEMA)
    assert frame.locked is None and frame.values[40] is None and frame.flags == ("LOCK_CODE_A",)


def test_vectorized_decoder_matches_scalar():
    rng = np.random.default_rng(4)
    frames = [encode_frame([int(rng.integers(0, c.full_scale_code + 1)) if not c.is_lock
                            else int(rng.integers(0, 2)) for c in SCHEMA.channels], SCHEMA) for _ in range(50)]
    payloads = [split_frame(frame, SCHEMA)[1] for frame in frames]
    codes, values = decode_many(payloads, SCHEMA)
    for row, frame in enumerate(frames):
        parsed = parse_frame(frame, SCHEMA)
        assert tuple(codes[row]) == parsed.codes
        np.testing.assert_allclose(values[row], [np.nan if v is None else v for v in parsed.values])
    np.testing.assert_allclose(values_from_codes(codes, SCHEMA), values)


def test_schema_is_consistent_and_hashed():
    assert sum(c.width for c in SCHEMA.channels) == 113 and len(SCHEMA.channels) == 41
    assert SCHEMA.version == "0.0.2" and len(SCHEMA.sha256) == 64
    assert SCHEMA.channels[39].kind == "analog" and SCHEMA.channels[40].kind == "lock"
    bad = {"frame": {"hex_characters": 4, "terminators": ["CRLF"]},
           "channels": [{"index": 0, "source_label": "a", "hex_offset": 0, "hex_width": 3, "source_gain": 1},
                        {"index": 1, "source_label": "b", "hex_offset": 4, "hex_width": 1, "source_gain": 1}]}
    with pytest.raises(T4SchemaError):
        schema_from_dict(bad)


# %% READ-ONLY QUERY REPLIES (T4S-MAN-0015 Sec. 1.1.2)
def test_query_replies():
    assert parse_query_reply(b"$RDFS ;=405750.927598720; \r\n", b"RDFS;\r\n") == "405750.927598720"
    assert parse_query_reply(b"$RDREG=DRIFTCOMP;=+1.5e-15;\r\n", b"RDREG=DRIFTCOMP;\r\n") == "+1.5e-15"
    with pytest.raises(T4ReplyError):
        parse_query_reply(b"!RDFS ;\r\n", b"RDFS;\r\n")
    with pytest.raises(ValueError):
        parse_query_reply(b"$SETFSREL=+1E-13;\r\n", b"SETFSREL=+1E-13;\r\n")
    assert synth_hz_from_reply("1420405750.927598720") == pytest.approx(405750.927598720)
    assert not any(command.split(b"=")[0].rstrip(b";\r\n") in (b"SETFSREL", b"DRIFTCOMP", b"UVAR", b"SETH",
                                                              b"ADJ1PPS", b"SYNCPPS", b"REBOOT")
                   and b"RDREG" not in command for command in READ_ONLY_QUERIES)


# %% UDP AGAINST THE LOOPBACK EMULATOR
def monitor_for(emulator, **overrides):
    host, port = emulator.address
    config = MonitorConfig(host, port, timeout_s=0.2, attempts=3, backoff=1.0, interval_s=0.2, **overrides)
    return T4Monitor(config)


def test_poll_success_and_only_allowed_commands():
    with T4Emulator() as emulator:
        monitor = monitor_for(emulator)
        record = monitor.poll_once()
        assert record.ok and record.frame.locked and record.rtt_ms > 0
        record, answer = monitor.query(b"RDFS;\r\n")
        assert answer == "405750.927598720"
        with pytest.raises(PermissionError):
            monitor.query(b"SETFSREL=+1E-13;\r\n")
        assert emulator.stats.rejected_commands == []
        assert set(monitor.sent_commands) <= set(READ_ONLY_QUERIES)


def test_timeout_then_success_and_exhaustion():
    with T4Emulator() as emulator:
        monitor = monitor_for(emulator)
        emulator.faults.drop = 1
        record = monitor.poll_once()
        assert record.ok and record.attempt == 2
        emulator.faults.drop = 10
        record = monitor.poll_once()
        assert record.status == "TIMEOUT" and record.attempt == 3


def test_wrong_peer_duplicate_garbage_truncation():
    with T4Emulator() as emulator:
        monitor = monitor_for(emulator)
        emulator.faults.wrong_peer = 1
        record = monitor.poll_once()
        assert record.ok and record.foreign_datagrams == 1 and record.attempt == 2
        emulator.faults.garbage = 1
        assert monitor.poll_once().status == "FRAME_ERROR:BAD_HEX"
        emulator.faults.truncate = 1
        assert monitor.poll_once().status == "FRAME_ERROR:TRUNCATED"
        emulator.faults.duplicate = 1
        assert monitor.poll_once().ok and monitor.poll_once().ok


def test_occupied_local_port_and_cancellation():
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("", 0))
    try:
        with T4Emulator() as emulator:
            monitor = monitor_for(emulator, local_port=blocker.getsockname()[1])
            assert monitor.poll_once().status == "PORT_IN_USE"
            monitor = monitor_for(emulator)
            cancel = threading.Event()
            cancel.set()
            assert monitor.poll_once(cancel).status == "CANCELLED"
    finally:
        blocker.close()


def test_non_loopback_needs_approval():
    with pytest.raises(PermissionError):
        T4Monitor(MonitorConfig("192.0.2.1"))           # TEST-NET-1, never contacted
    T4Monitor(MonitorConfig("192.0.2.1", read_only_approved=True))


def test_type2_cr_terminator_end_to_end():
    with T4Emulator(terminator=b"\r") as emulator:
        record = monitor_for(emulator).poll_once()
        assert record.ok and record.frame.terminator == b"\r"


# %% RECORDER, RAM CONTROL AND RE-DECODING
def test_recorder_raw_first_flush_offload_and_reload(tmp_path):
    with T4Emulator() as emulator:
        monitor = monitor_for(emulator)
        recorder = T4Recorder(tmp_path, SCHEMA, monitor.config, preview_length=3)
        emulator.faults.garbage = 1
        assert run_loop(monitor, recorder, threading.Event(), count=5) == 5
    assert recorder.counts == {"polls": 5, "ok": 4, "bad": 1, "write_errors": 0, "offloads": 0}
    assert recorder.folder.name.startswith("T4_telemetry_") and "_" not in recorder.folder.name[13:]
    assert len(recorder.preview_mjd) == 3                      # bounded preview
    assert recorder.offload_pickle().exists() and len(recorder.preview_mjd) == 0
    telemetry = load_t4_telemetry([recorder.folder / "t4_raw_frames.csv"])
    assert len(telemetry.mjd) == 4 and telemetry.rejected == {"BAD_HEX": 1}
    again = load_t4_telemetry([recorder.folder / "t4_telemetry.csv"])
    np.testing.assert_allclose(again.values, telemetry.values, rtol=1e-12, equal_nan=True)


def test_disk_failure_keeps_running(tmp_path):
    with T4Emulator() as emulator:
        monitor = monitor_for(emulator)
        recorder = T4Recorder(tmp_path, SCHEMA, monitor.config)
        recorder.values_path.unlink()
        recorder.values_path.mkdir()                            # writing now fails
        assert recorder.write(monitor.poll_once()) is False
    assert recorder.counts["write_errors"] == 1 and len(recorder.preview_mjd) == 1


def test_raw_escape_roundtrip_and_text_log(tmp_path):
    assert unescape_raw(escape_raw(VALID)) == VALID
    log = tmp_path / "t4.log"
    log.write_text("# comment\n2026-11-03T06:31:15-05:00 " + MANUAL_EXAMPLE.decode() + "\n"
                   "61347.5 " + MANUAL_EXAMPLE.decode() + "\n"
                   "2026-11-03T06:31:15 " + MANUAL_EXAMPLE.decode() + "\n", encoding="utf-8")
    telemetry = load_t4_telemetry([log])
    assert len(telemetry.mjd) == 2 and telemetry.rejected == {"BAD_TIME": 1}
    assert telemetry.mjd[0] == pytest.approx(61347.0 + (11 * 3600 + 31 * 60 + 15) / 86400, abs=1e-9)


def test_imports_are_inert():
    before = threading.active_count()
    import importlib, t4_protocol, t4_monitor, t4_emulator   # noqa: E401
    for module in (t4_protocol, t4_monitor, t4_emulator):
        importlib.reload(module)
    assert threading.active_count() == before


# %% T4 SETTINGS (T4S-MAN-0012 Eq. 12; T4S-MAN-0015 SETFSREL / DRIFTCOMP)
def t4_series(slope_ns_per_day=-2.719, curvature=0.0):
    days = np.linspace(0, 30, 721)
    phase = (-357.49 + slope_ns_per_day * days + curvature * days ** 2) * 1e-9
    return TimingSeries(61316.64903 + days, phase, "T4 -> CNS_GPS1", "FITS")


def test_t4_setfsrel_sign_quantum_and_prediction():
    fit = fit_phase(t4_series(), order=1, maser="T4")
    out = t4_settings(fit, 61346.0, 1, current_synth_hz=405750.927598720)
    y = -2.719e-9 / 86400
    assert out["residual_fractional_frequency"] == pytest.approx(y, rel=1e-9)
    # T4 slow (y < 0): raise the output -> synthesizer DOWN (Eq. 12 negative sign).
    assert out["synth_change_hz_unrounded"] == pytest.approx(y * T4_SYNTH_SCALE_HZ, rel=1e-9)
    assert out["synth_steps"] == round(y * 1.42e9 / (5e6 / 2 ** 39)) < 0
    assert abs(out["quantization_residual"]) <= 0.5 * T4_SYNTH_STEP_HZ / T4_SYNTH_SCALE_HZ + 1e-30
    assert out["setfsrel"] > 0 and out["setfsrel_command"].startswith("SETFSREL=+")
    assert out["predicted_synth_hz"] == pytest.approx(405750.927598720 + out["synth_change_hz"])
    phase = t4_settings(fit, 61346.0, 1, setfsrel_convention="phase")
    assert phase["setfsrel"] == pytest.approx(-out["setfsrel"])
    assert out["drift"] is None


def test_t4_driftcomp_and_ranges():
    fit = fit_phase(t4_series(curvature=0.002), order=2, maser="T4")
    out = t4_settings(fit, 61346.0, 1, current_driftcomp=1e-16)
    drift = 2 * 0.002e-9 / 86400
    assert out["drift"]["measured_residual_drift_per_day"] == pytest.approx(drift, rel=1e-6)
    assert out["drift"]["new_driftcomp"] == pytest.approx(1e-16 - drift, rel=1e-6)
    assert out["drift"]["command"].startswith("DRIFTCOMP=")
    tiny = t4_settings(fit_phase(t4_series(slope_ns_per_day=1e-6), order=1, maser="T4"), 61346.0, 1)
    assert tiny["synth_steps"] == 0 and tiny["setfsrel_command"] is None
