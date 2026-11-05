# -*- coding: utf-8 -*-
"""
=============================================================================
# %% Header Info
--------

Created on 2025-06-04

# %%% Author Information
@author: William W. Wallace (NRAO/GBO)

# %%% Revisions
--------
Utilizing Semantic Schema as External Release.Internal Release.Working version

# %%%% 0.0.1: Script to run in console
Date: 2025-06-04
    Taken directly from the iScience / Safran group script: binds local UDP
    port 14000, sends REBOOT;, then waits for a reply.

# %%%% 0.0.2: NaN
Date: (not recorded)

# %%%% 0.0.3: Safe reboot procedure (superseded, do not use)
Date: 2026-11-05
    Read-only checks before and after REBOOT, typed confirmation.
    Defect: used an ephemeral local port. The T4 appears to answer on local
    port 14000, so 0.0.3 would time out at the pre-check (nothing sent).

# %%%% 0.0.4: Corrections after verification against T4S-MAN-0015
Date: 2026-11-05
    - Local port 14000 bound again, as in the vendor script and
      T4_Maser_Comms.py. LOCAL_PORT = 0 selects an ephemeral port if a
      site test shows the maser answers the source port instead.
    - Answers checked: "$<command>..." accepted, "!<command>" reported as
      an error [1, Sec. 1.1.2]; late or unrelated datagrams ignored.
    - REBOOT has no answer [1, Table 1]: silence is the expected result.
      A "!REBOOT" answer means it was rejected, and the script stops.
    - Settings are read when each command is sent, not when the module
      is imported; the host name is resolved once per exchange.
    - Windows UDP connection-reset reports disabled (SIO_UDP_CONNRESET).

# %%%%% Function Descriptions
    command_key: the part of a command that the maser echoes in its answer
    open_socket: UDP socket bound to LOCAL_ADDRESS:LOCAL_PORT
    exchange: send one command, return the matching answer
    send_read_only: send only allowlisted read-only queries
    confirm_reboot: ask the operator to type REBOOT
    send_reboot: send REBOOT once and describe the outcome
    run_checks: run the read-only checks and print the answers
    main: pre-check, confirm, reboot, wait, post-check, compare

# %%%%% Variable Descriptions
    SERVER_ADDRESS: LAN module IP address (set per maser; confirm first)
    SERVER_PORT: UDP port of the LAN module (14000)
    LOCAL_ADDRESS: local interface to bind ("" = all interfaces)
    LOCAL_PORT: local UDP port the maser answers (14000; 0 = ephemeral)
    TIMEOUT_S: seconds to wait for each answer
    BUFFER_BYTES: receive buffer size (larger than any query answer)
    REBOOT_WAIT_S: seconds to wait after REBOOT before the post-check
    READ_ONLY_QUERIES: the only commands send_read_only will send
    PRE_CHECKS: read-only checks run before and after the reboot
    REBOOT_COMMAND: the reboot command [1, Table 1, cmd 9]

# %%%%% More Info
    Run where no other program holds local UDP port 14000: close the
    workbench T4 tab and any T4_Maser_Comms.py session first.
    Standard library only (Python 3.8 or later; tested on 3.12).
    Run from Spyder or:  python T4_Reboot.py

    [1] T4Science SA, T4S-Monitoring Network Software User Manual,
        T4S-MAN-0015, ver. 1.17, Jul. 19, 2022: Table 1 (p. 8),
        Sec. 1.1.2 (p. 11), Sec. 1.1.3 (p. 13), Sec. 5 (p. 21),
        Sec. 6.7 (p. 24).
=============================================================================
"""

# %% Imports
import socket
import time

# %% Settings
SERVER_ADDRESS = "10.16.98.16"   # LAN module address; set per maser [1, Sec. 5]
SERVER_PORT = 14000              # UDP port of the LAN module [1, Secs. 1.1.2, 5]
LOCAL_ADDRESS = ""               # local interface to bind ("" = all)
LOCAL_PORT = 14000               # local port the maser answers (0 = ephemeral)
TIMEOUT_S = 2.0                  # seconds to wait for each answer
BUFFER_BYTES = 4096              # larger than any expected query answer

# The manual gives no wait for REBOOT. 30 s is its wait after a front-panel
# reset of the LAN module [1, Sec. 6.7], used here as a reasonable minimum.
REBOOT_WAIT_S = 30.0

# Read-only queries [1, Table 1, cmds 2, 8, 13]. send_read_only refuses
# anything else, matching the workbench's read-only allowlist.
READ_ONLY_QUERIES = ("GETID;\r\n", "RDFS;\r\n", "RDREG=DRIFTCOMP;\r\n")

# Checks run before and after the reboot, in this order.
PRE_CHECKS = READ_ONLY_QUERIES

# Reboot of the M&C software; the manual lists no answer [1, Table 1, cmd 9].
REBOOT_COMMAND = "REBOOT;\r\n"


# %% Errors
class MaserReplyError(RuntimeError):
    """The maser answered "!<command>": it rejected the command [1, Sec. 1.1.2]."""


# %% Communication
def command_key(command):
    """Return the part of a command that the maser echoes in its answer.

    "RDREG=DRIFTCOMP;\\r\\n" -> "RDREG=DRIFTCOMP"; "RDFS;\\r\\n" -> "RDFS".
    Spaces are removed because the manual's own examples vary, for example
    "$RDFS ;=[value];" and "$GETID;= Monitoring NDCU Ver. 2.0;".
    """
    return command.split(";")[0].replace(" ", "")


def open_socket():
    """Return a UDP socket bound to LOCAL_ADDRESS:LOCAL_PORT.

    Raises:
        OSError: the local port could not be bound (usually because another
            program already holds it).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if hasattr(socket, "SIO_UDP_CONNRESET"):
            # Windows only: stop an ICMP port-unreachable from surfacing as
            # ConnectionResetError on later receives.
            sock.ioctl(socket.SIO_UDP_CONNRESET, False)
        sock.bind((LOCAL_ADDRESS, LOCAL_PORT))
    except OSError as err:
        sock.close()
        raise OSError(
            f"cannot bind local UDP port {LOCAL_PORT}: {err}. Close any other "
            f"program using it (workbench T4 tab, T4_Maser_Comms.py)") from err
    return sock


def exchange(command, expect_reply=True, timeout_s=None):
    """Send one ASCII command and return the maser's matching answer.

    Only datagrams from the maser's address are considered. An answer that
    starts with "$" plus the command key is returned; "!" plus the key
    raises MaserReplyError; anything else (a late answer to an earlier
    command, for example) is ignored until the deadline. The socket is
    always closed.

    Args:
        command: the full command, including ";" and the CR LF terminator.
        expect_reply: if False, silence until the deadline returns None
            instead of raising (used for REBOOT, which has no answer).
        timeout_s: seconds to wait; TIMEOUT_S if None.

    Raises:
        socket.timeout: no matching answer in time (expect_reply True).
        MaserReplyError: the maser rejected the command.
        OSError: name resolution, bind or send failed.
    """
    timeout_s = TIMEOUT_S if timeout_s is None else timeout_s
    server_ip = socket.gethostbyname(SERVER_ADDRESS)
    key = command_key(command)
    sock = open_socket()
    try:
        sock.sendto(command.encode("ascii"), (server_ip, SERVER_PORT))
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if expect_reply:
                    raise socket.timeout(
                        f"no answer to {key} within {timeout_s} s")
                return None
            sock.settimeout(remaining)
            try:
                data, addr = sock.recvfrom(BUFFER_BYTES)
            except socket.timeout:
                continue                  # the loop re-checks the deadline
            except ConnectionResetError:
                time.sleep(0.05)          # Windows ICMP report; keep waiting
                continue
            if addr[0] != server_ip:
                continue                  # datagram from another host
            text = data.decode("ascii", errors="replace").strip()
            compact = text.replace(" ", "")
            if compact.startswith("!" + key):
                raise MaserReplyError(f"maser rejected {key}: {text}")
            if compact.startswith("$" + key):
                return text
            # Otherwise: a late or unrelated answer. Ignore it and keep waiting.
    finally:
        sock.close()


def send_read_only(command):
    """Send a read-only query and return its answer.

    Raises:
        PermissionError: the command is not in READ_ONLY_QUERIES.
    """
    if command not in READ_ONLY_QUERIES:
        raise PermissionError(f"refusing non-read-only command {command!r}")
    return exchange(command)


# %% Reboot
def confirm_reboot():
    """Return True only if the operator types REBOOT."""
    typed = input("Type REBOOT to reboot the maser's M&C software, "
                  "or press Enter to cancel: ")
    return typed.strip() == "REBOOT"


def send_reboot():
    """Send REBOOT once and return a description of the outcome.

    The command is never retried: a second REBOOT would restart the M&C
    software again.

    Raises:
        MaserReplyError: the maser answered "!REBOOT" (rejected).
        OSError: bind or send failed; REBOOT may not have been sent.
    """
    reply = exchange(REBOOT_COMMAND, expect_reply=False)
    if reply is None:
        return "no answer (expected: the manual lists no answer for REBOOT)"
    return f"answer: {reply}"


# %% Checks
def run_checks(label):
    """Send each read-only check, print the answer, return {query: answer}."""
    replies = {}
    for query in PRE_CHECKS:
        replies[query] = send_read_only(query)
        print(f"[{label}] {query.strip():<16} -> {replies[query]}")
    return replies


# %% Main
def main():
    """Pre-check, confirm, one REBOOT, wait, post-check, compare.

    Returns a process exit code:
        0  success, or cancelled by the operator (nothing sent)
        1  pre-check failed (REBOOT not sent)
        2  post-check failed (the M&C software may still be restarting)
        3  REBOOT rejected, or the send failed
    """
    print(f"Maser LAN module {SERVER_ADDRESS}:{SERVER_PORT}, "
          f"local port {LOCAL_PORT or 'ephemeral'}")

    # Step 1: read-only checks. If any fails, REBOOT is not sent.
    try:
        before = run_checks("before")
    except (OSError, MaserReplyError) as err:
        print(f"Pre-check failed; REBOOT was not sent: {err}")
        return 1

    # Step 2: one REBOOT, only after typed confirmation.
    if not confirm_reboot():
        print("Cancelled. REBOOT was not sent.")
        return 0
    try:
        outcome = send_reboot()
    except MaserReplyError as err:
        print(f"{err}. The maser did not reboot.")
        return 3
    except OSError as err:
        print(f"Sending REBOOT failed: {err}. Run GETID to see whether the "
              f"maser restarted.")
        return 3
    print(f"REBOOT sent once: {outcome}")

    # Step 3: wait for the restart, then read the same values back.
    print(f"Waiting {REBOOT_WAIT_S:.0f} s for the M&C software to restart ...")
    time.sleep(REBOOT_WAIT_S)
    try:
        after = run_checks("after")
    except (OSError, MaserReplyError) as err:
        print(f"Post-check failed: {err}. The M&C software may still be "
              f"restarting; run GETID again in a minute.")
        return 2

    # Step 4: compare. RDFS can change on its own when drift compensation
    # steps the synthesizer, so a change is a prompt to look, not a fault.
    print()
    for query in PRE_CHECKS:
        state = "unchanged" if before[query] == after[query] else "CHANGED"
        print(f"{query.strip():<16} {state}")
    print("Also check that the web page updates, that recording is still on "
          "(new records in Monitoring), and that 1PPS is present.")
    return 0


# %% Entry point
if __name__ == "__main__":
    raise SystemExit(main())
