#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 William W. Wallace
# %% ===========================================================================
#    MODULE HEADER
# =============================================================================
"""Pure, strict decoder for T4 iMaser MONIT replies (no sockets, no GUI).

===============================================================================
 Title          : T4 MONIT Frame Decoder
 Filename       : t4_protocol.py
 Version        : 0.0.2 (Semantic Versioning 2.0.0)
 Author         : William W. Wallace
 Company        : NRAO/GBO
 Created        : 2026-11-03
 Modified       : 2026-11-03
 Language       : Python 3.12 (parses at 3.8)
 Requires       : numpy (cupy optional, only for decode_many(use_gpu=True))
 Licence        : GPL-3.0-or-later; see LICENSE
 Status         : Layout, widths and gains verified against the OEM manual
                  T4S-MAN-0012 1.27, Table 6 [3], and its example replies;
                  not yet confirmed with captures from the GBO unit.
 Revision       : 0.0.1 Strict bytes framing, versioned schema with hash,
                  raw-code retention, vectorized many-frame decoder, encoder
                  for the emulator and tests (source-script layout [1]).
                  0.0.2 Schema 0.0.2 from the OEM manual [3]: CR LF (Type 1,
                  Card FPGA) or CR (Type 2, IO card LAN) terminators;
                  channel 40 is an analog lock-detector voltage; channel 41
                  is the main-PLL lock bit (0/1, other codes flagged);
                  read-only query allowlist and reply parser [4].
===============================================================================

WHAT THIS MODULE DOES
---------------------
A MONIT reply is ``$`` + 113 ASCII hexadecimal characters + CR LF (Type 1,
Card FPGA T4S-724) or CR (Type 2, IO card LAN T4S-711) [3, Sec. 9.13]. The
113 characters are 32 channels of 12 bits (3 hex digits), 8 channels of
8 bits (2 hex digits) and the main-PLL lock bit (1 hex digit):
32*3 + 8*2 + 1 = 113 [3, Table 6]. Every field is read as an UNSIGNED code
c_i = int(field, 16) and the engineering value is v_i = g_i * c_i with the
Table 6 gain g_i [1], [3]; the -15 V rail has a negative gain.

Rules that differ on purpose from the source script [1] (see the T4 plan):

* bytes in, typed exceptions out: never ``str(bytes)`` heuristics, never a
  dialog, never ``sys.exit``;
* the frame is checked exactly: prefix, length, hex alphabet, terminator,
  no trailing bytes. Text before ``$`` is an error unless the caller
  explicitly allows it (and the prefix is then reported);
* channels marked "Not used" in Table 6 (C field, -5 V, +18 V) are KEPT
  with their raw code, ``available=False`` and value None (NaN in arrays);
* channel 40 "LOCK 100 MHz status" is an 8-bit analog voltage (gain
  3.906E-02 V/LSB) [3]; channel 41 is the main-PLL lock bit: 1 locked,
  0 unlocked [3]. Any other code is reported in ``T4Frame.flags``.

Nothing in this module runs on import.

REFERENCES
----------
[1] W. W. Wallace, "T4_Maser_Comms.py," GBO_Maser_Control, commit
    9682a2ffbcd0b0df6612d4aef3619d7bdaf6cc66. https://raw.githubusercontent.
    com/Braveheart53/GBO_Maser_Control/main/T4_Maser_Comms.py
[2] Safran, "iMaser 3000," datasheet, Mar. 11, 2024. https://safran-navigation-
    timing.com/wp-content/uploads/2024/03/SAFRAN_IMASER-3000_Datasheet_3-11-2024_US.pdf
[3] T4Science, "iMaser 3000 Installation, Operation and Maintenance User
    Manual," T4S-MAN-0012, issue 1.27, May 2, 2022, Sec. 9.13, Table 6.
[4] T4Science, "T4S-Monitoring network Software User Manual," T4S-MAN-0015,
    ver. 1.17, Jul. 19, 2022, Secs. 1.1.1-1.1.3.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence
import hashlib
import json
import math

import numpy as np

__version__ = "0.0.2"

#: The monitoring query [4, Sec. 1.1.1, command 19].
MONIT_COMMAND = b"MONIT;\r\n"
#: Read-only queries documented in [4]; nothing that writes is listed.
READ_ONLY_QUERIES = (MONIT_COMMAND, b"RDFS;\r\n", b"GETID;\r\n", b"RDREG=DRIFTCOMP;\r\n",
                     b"RDREG=LSBWEIGHT;\r\n", b"RDREG=UVAR;\r\n", b"RDREG=SETH;\r\n",
                     b"RDREG=ADJ1PPS;\r\n")
#: Default schema shipped with the package.
DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parent / "t4" / "t4_monit_schema_v0.0.2.json"
_HEX_DIGITS = frozenset(b"0123456789abcdefABCDEF")


# %% ===========================================================================
#    ERRORS
# =============================================================================
class T4FrameError(ValueError):
    """A MONIT reply failed strict framing. ``reason`` is a stable code.

    Codes: NOT_BYTES, EMPTY, NON_ASCII, NO_PREFIX, UNEXPECTED_PREFIX,
    NO_TERMINATOR, BAD_TERMINATOR, TRUNCATED, OVERSIZED, BAD_HEX,
    EXTRA_BYTES.
    """

    def __init__(self, reason: str, message: str):
        super().__init__(f"{reason}: {message}")
        self.reason = reason


class T4SchemaError(ValueError):
    """The channel schema is inconsistent (gaps, overlaps, wrong length)."""


# %% ===========================================================================
#    SCHEMA
# =============================================================================
@dataclass(frozen=True)
class T4Channel:
    """One MONIT field. ``gain`` None means 'no engineering value'."""
    index: int
    label: str
    short_name: str
    offset: int
    width: int
    gain: float | None
    unit: str
    group: str
    kind: str                       # "analog" or "lock"
    verified: bool

    @property
    def available(self) -> bool:
        """True when an analog engineering value can be formed."""
        return self.kind == "analog" and self.gain is not None and math.isfinite(self.gain)

    @property
    def is_lock(self) -> bool:
        return self.kind == "lock"

    @property
    def full_scale_code(self) -> int:
        """Largest unsigned code the field can hold (16**width - 1)."""
        return 16 ** self.width - 1


@dataclass(frozen=True)
class T4Schema:
    """Versioned channel table with a content hash for provenance."""
    version: str
    status: str
    hex_characters: int
    prefix: bytes
    terminators: tuple[bytes, ...]
    channels: tuple[T4Channel, ...]
    sha256: str
    source: dict = field(default_factory=dict, compare=False)

    def channel(self, index: int) -> T4Channel:
        return self.channels[index]

    @property
    def analog(self) -> tuple[T4Channel, ...]:
        return tuple(item for item in self.channels if item.kind == "analog")

    @property
    def groups(self) -> list[str]:
        """Functional groups in first-appearance order."""
        seen: list[str] = []
        for item in self.channels:
            if item.group not in seen:
                seen.append(item.group)
        return seen


def _canonical_hash(payload: dict) -> str:
    """SHA-256 of the canonical JSON text (sorted keys, no whitespace)."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def schema_from_dict(payload: dict) -> T4Schema:
    """Build and validate a schema; raise T4SchemaError on any inconsistency."""
    frame = payload.get("frame", {})
    total = int(frame.get("hex_characters", 0))
    names = frame.get("terminators", [frame.get("terminator", "CRLF")])
    mapping = {"CRLF": b"\r\n", "LF": b"\n", "CR": b"\r"}
    if not names or any(str(name) not in mapping for name in names):
        raise T4SchemaError("frame.terminators must list CRLF, LF and/or CR.")
    # Longest first, so CR LF is matched before a bare CR.
    terminators = tuple(sorted({mapping[str(name)] for name in names}, key=len, reverse=True))
    prefix = str(frame.get("prefix", "$")).encode("ascii")
    channels = []
    expected_offset = 0
    for position, item in enumerate(sorted(payload["channels"], key=lambda entry: entry["index"])):
        if int(item["index"]) != position:
            raise T4SchemaError(f"channel indices must be 0..N-1 without gaps (found {item['index']}).")
        offset, width = int(item["hex_offset"]), int(item["hex_width"])
        if offset != expected_offset or width < 1:
            raise T4SchemaError(f"channel {position}: offset {offset} width {width} leaves a gap or overlap.")
        expected_offset += width
        gain = item.get("source_gain")
        gain = None if gain is None else float(gain)
        channels.append(T4Channel(
            index=position, label=str(item["source_label"]),
            short_name=str(item.get("short_name", f"T4_{position:02d}")),
            offset=offset, width=width, gain=gain, unit=str(item.get("unit", "raw")),
            group=str(item.get("group", "Other")), kind=str(item.get("kind", "analog")),
            verified=bool(item.get("engineering_gain_verified", False))))
    if expected_offset != total:
        raise T4SchemaError(f"channel widths sum to {expected_offset}, frame declares {total}.")
    return T4Schema(version=str(payload.get("schema_version", payload.get("version", "?"))),
                    status=str(payload.get("status", "")), hex_characters=total,
                    prefix=prefix, terminators=terminators, channels=tuple(channels),
                    sha256=_canonical_hash(payload),
                    source={key: payload.get(key) for key in ("source_url", "source_commit", "derived_from",
                                                              "manual", "network_manual")})


def load_schema(path: str | Path | None = None) -> T4Schema:
    """Read a schema JSON (default: the shipped 0.0.1 schema)."""
    path = Path(path) if path else DEFAULT_SCHEMA_PATH
    return schema_from_dict(json.loads(path.read_text(encoding="utf-8")))


# %% ===========================================================================
#    ONE FRAME
# =============================================================================
@dataclass
class T4Frame:
    """Decoded reply. Raw codes are always kept; values may be None."""
    codes: tuple[int, ...]
    values: tuple[float | None, ...]
    hex_payload: str
    prefix_bytes: bytes
    schema_version: str
    schema_sha256: str
    terminator: bytes = b""
    flags: tuple[str, ...] = ()

    @property
    def locked(self) -> bool | None:
        """Main-PLL lock (channel 41): True, False, or None if the code is invalid."""
        code = self.codes[-1]
        return None if code not in (0, 1) else bool(code)

    def as_dict(self, schema: T4Schema) -> dict[str, Any]:
        """Flat mapping short_name -> value, plus <short_name>_code."""
        output: dict[str, Any] = {}
        for channel, code, value in zip(schema.channels, self.codes, self.values):
            output[channel.short_name] = value
            output[channel.short_name + "_code"] = code
        return output


def split_frame(data: bytes, schema: T4Schema, *, require_terminator: bool = True,
                allow_prefix: bool = False) -> tuple[bytes, str]:
    """Validate framing; return (text before '$', hex payload).

    Parameters
    ----------
    data:
        The reply exactly as received (bytes). ``str`` is refused.
    require_terminator:
        True for live datagrams. False for log lines already stripped of
        their line ending; a terminator, if present, must still be exact.
    allow_prefix:
        Accept (and report) text before the '$'. Off by default: the
        source script strips such text [1] but no capture shows it exists.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise T4FrameError("NOT_BYTES", f"expected bytes, got {type(data).__name__}")
    data = bytes(data)
    if not data:
        raise T4FrameError("EMPTY", "empty reply")
    try:
        data.decode("ascii")
    except UnicodeDecodeError as error:
        raise T4FrameError("NON_ASCII", f"byte {error.start} is not ASCII") from None
    position = data.find(schema.prefix)
    if position < 0:
        raise T4FrameError("NO_PREFIX", f"no {schema.prefix!r} delimiter")
    if position > 0 and not allow_prefix:
        raise T4FrameError("UNEXPECTED_PREFIX", f"{position} byte(s) before {schema.prefix!r}")
    prefix_bytes, body = data[:position], data[position + len(schema.prefix):]
    found = next((end for end in schema.terminators if body.endswith(end)), b"")
    if found:
        body = body[:-len(found)]
    if body.endswith((b"\r", b"\n")):          # e.g. a bare LF, or LF CR
        raise T4FrameError("BAD_TERMINATOR", f"expected one of {schema.terminators!r}")
    if not found and require_terminator:
        raise T4FrameError("NO_TERMINATOR", f"missing one of {schema.terminators!r}")
    count = schema.hex_characters
    hex_run = 0
    while hex_run < len(body) and body[hex_run] in _HEX_DIGITS:
        hex_run += 1
    if hex_run < len(body):
        # Distinguish a bad character inside the payload from trailing bytes.
        if hex_run < count:
            raise T4FrameError("BAD_HEX", f"non-hex byte {body[hex_run:hex_run + 1]!r} at payload position {hex_run}")
        raise T4FrameError("EXTRA_BYTES", f"{len(body) - count} byte(s) after the {count}-character payload")
    if len(body) < count:
        raise T4FrameError("TRUNCATED", f"{len(body)} of {count} hex characters")
    if len(body) > count:
        raise T4FrameError("OVERSIZED", f"{len(body)} hex characters, expected {count}")
    return prefix_bytes, body.decode("ascii"), found


def parse_frame(data: bytes, schema: T4Schema, *, require_terminator: bool = True,
                allow_prefix: bool = False) -> T4Frame:
    """Strictly parse one MONIT reply into raw codes and provisional values."""
    prefix_bytes, payload, terminator = split_frame(data, schema, require_terminator=require_terminator,
                                                    allow_prefix=allow_prefix)
    codes, values, flags = [], [], []
    for channel in schema.channels:
        code = int(payload[channel.offset:channel.offset + channel.width], 16)
        codes.append(code)
        if channel.available:
            values.append(code * channel.gain)
        elif channel.is_lock:
            values.append(float(code) if code in (0, 1) else None)
            if code not in (0, 1):
                flags.append(f"LOCK_CODE_{code:X}")
        else:
            values.append(None)
    return T4Frame(tuple(codes), tuple(values), payload, prefix_bytes, schema.version, schema.sha256,
                   terminator, tuple(flags))


def encode_frame(codes: Sequence[int], schema: T4Schema, *, terminator: bool | bytes = True,
                 uppercase: bool = True) -> bytes:
    """Inverse of parse_frame for the emulator and tests (codes must fit)."""
    if len(codes) != len(schema.channels):
        raise ValueError(f"need {len(schema.channels)} codes, got {len(codes)}")
    parts = []
    for channel, code in zip(schema.channels, codes):
        code = int(code)
        if not 0 <= code <= channel.full_scale_code:
            raise ValueError(f"channel {channel.index}: code {code} outside 0..{channel.full_scale_code}")
        text = f"{code:0{channel.width}x}"
        parts.append(text.upper() if uppercase else text)
    end = terminator if isinstance(terminator, bytes) else (schema.terminators[0] if terminator else b"")
    return schema.prefix + "".join(parts).encode("ascii") + end


def code_for_value(value: float, channel: T4Channel) -> int:
    """Nearest valid code for an engineering value (emulator helper)."""
    if not channel.available:
        return 0
    return int(min(max(round(value / channel.gain), 0), channel.full_scale_code))


def status_bits(code: int, width_hex: int) -> tuple[int, ...]:
    """Raw bits, most significant first, of a status code. No meaning implied."""
    bits = 4 * width_hex
    return tuple((int(code) >> (bits - 1 - shift)) & 1 for shift in range(bits))


# %% ===========================================================================
#    MANY FRAMES AT ONCE (VECTORIZED; OPTIONAL GPU)
# =============================================================================
def _array_module(use_gpu: bool):
    """numpy, or cupy when requested AND importable with a working device."""
    if use_gpu:
        try:
            import cupy  # type: ignore
            cupy.cuda.runtime.getDeviceCount()
            return cupy
        except Exception:                       # noqa: BLE001 - fall back silently
            return np
    return np


def decode_many(payloads: Iterable[str], schema: T4Schema, *, use_gpu: bool = False
                ) -> tuple[np.ndarray, np.ndarray]:
    """Decode already-validated hex payloads in one vectorized pass.

    Returns (codes int64 [N, channels], values float64 [N, channels]) with
    NaN where no engineering value exists. Each payload must be exactly the
    schema length of hex characters (use split_frame first); a wrong length
    raises ValueError. With ``use_gpu`` the arithmetic runs on CuPy when a
    CUDA device is present, else on NumPy; the results are NumPy arrays.
    """
    xp = _array_module(use_gpu)
    payloads = list(payloads)
    width = schema.hex_characters
    if not payloads:
        return (np.empty((0, len(schema.channels)), np.int64),
                np.empty((0, len(schema.channels)), float))
    raw = np.frombuffer("".join(payloads).encode("ascii"), dtype=np.uint8)
    if raw.size != width * len(payloads):
        raise ValueError("every payload must have exactly the schema length")
    lookup = np.full(256, 255, dtype=np.uint8)
    for digit in b"0123456789":
        lookup[digit] = digit - 48
    for digit in b"abcdef":
        lookup[digit] = digit - 87
    for digit in b"ABCDEF":
        lookup[digit] = digit - 55
    nibbles = lookup[raw].reshape(len(payloads), width)
    if (nibbles == 255).any():
        raise ValueError("non-hex character in a payload")
    nibbles = xp.asarray(nibbles, dtype=xp.int64)
    codes = xp.zeros((len(payloads), len(schema.channels)), dtype=xp.int64)
    for channel in schema.channels:
        block = nibbles[:, channel.offset:channel.offset + channel.width]
        weights = xp.asarray([16 ** (channel.width - 1 - k) for k in range(channel.width)], dtype=xp.int64)
        codes[:, channel.index] = block @ weights
    gains = xp.asarray([channel.gain if channel.available else (1.0 if channel.is_lock else np.nan)
                        for channel in schema.channels])
    values = codes.astype(float) * gains[None, :]
    for channel in schema.channels:
        if channel.is_lock:                      # only 0/1 are valid lock codes
            column = values[:, channel.index]
            values[:, channel.index] = xp.where((column == 0) | (column == 1), column, xp.nan)
    if xp is not np:
        codes, values = xp.asnumpy(codes), xp.asnumpy(values)
    return np.asarray(codes), np.asarray(values)


def values_from_codes(codes: np.ndarray, schema: T4Schema) -> np.ndarray:
    """Engineering values for stored raw codes (NaN where none exists)."""
    codes = np.asarray(codes, dtype=np.int64).reshape(-1, len(schema.channels))
    values = np.full(codes.shape, np.nan)
    for channel in schema.channels:
        column = codes[:, channel.index]
        if channel.available:
            values[:, channel.index] = column * channel.gain
        elif channel.is_lock:
            values[:, channel.index] = np.where((column == 0) | (column == 1), column, np.nan)
    return values


def column_names(schema: T4Schema) -> list[str]:
    """Human-readable column titles 'T4_00 U batt.A [V]' for CSV/plots."""
    return [f"{channel.short_name} {channel.label}" for channel in schema.channels]


# %% ===========================================================================
#    READ-ONLY QUERY REPLIES  [4, Sec. 1.1.2]
# =============================================================================
class T4ReplyError(ValueError):
    """A query reply was an error (``!`` prefix) or not the expected answer."""


def query_name(command: bytes) -> str:
    """'RDREG=DRIFTCOMP' for b'RDREG=DRIFTCOMP;\\r\\n'."""
    return command.decode("ascii").strip().rstrip(";").strip()


def parse_query_reply(data: bytes, command: bytes) -> str:
    """Return the answer text of a read-only query reply.

    Answers have the form ``$<name>;=<value>;`` or ``$<name>=<value>;``
    [4]; spaces are tolerated. ``!<command>`` is the maser's error reply.
    Examples: ``$RDFS ;=405751.123456789;`` -> ``405751.123456789``;
    ``$RDREG=DRIFTCOMP;=+1.5e-15;`` -> ``+1.5e-15``.
    """
    if command not in READ_ONLY_QUERIES or command == MONIT_COMMAND:
        raise ValueError(f"{command!r} is not a documented read-only query")
    text = bytes(data).decode("ascii", "replace").strip()
    if text.startswith("!"):
        raise T4ReplyError(f"maser rejected {query_name(command)}: {text}")
    compact = text.replace(" ", "")
    name = query_name(command).replace(" ", "")
    if not compact.startswith("$" + name):
        raise T4ReplyError(f"unexpected reply to {name}: {text[:60]!r}")
    remainder = compact[len(name) + 1:]
    if "=" not in remainder:
        raise T4ReplyError(f"no value in reply to {name}: {text[:60]!r}")
    return remainder.split("=", 1)[1].split(";")[0].strip()


def synth_hz_from_reply(answer: str) -> float:
    """Synthesizer frequency in Hz from an RDFS answer.

    The 405 kHz synthesizer value is returned as is. A value near the
    hydrogen frequency (the web page's FREQ field) is reduced by
    284 x 5 MHz = 1.42 GHz [3, Eqs. 10-12].
    """
    value = float(answer)
    return value - 1.42e9 if value > 1e9 else value
