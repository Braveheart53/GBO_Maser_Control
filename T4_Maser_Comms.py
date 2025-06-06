# -*- coding: utf-8 -*-
# =============================================================================
# Author: William W. Wallace
# Author Phone: +1-(304) 456-2216
# Authore Email: wwallace@nrao.edu
# Date of Creation: 2025-05-29
# Date of last Edit:
# Purpose Of Script:
# Function Definitions:
#   Func1
# Update Log:
#     Entry 1
#     Text here
# Command Line Entry Format
#   T4_Maser_Comms_1p0p0.py 10.16.98.16 14000 "MONIT;\r\n" --retries 5 --timeout 2 --backoff 2
#
# =============================================================================
import argparse
import logging
import socket
import sys
import tkinter as tk
from typing import Optional
from tkinter import messagebox


def configure_logging(verbose: bool) -> None:
    """Configure logging based on verbosity level"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format='%(asctime)s - %(levelname)s - %(message)s',
        level=level
    )


def show_error_dialog(title, message):
    root = tk.Tk()
    root.withdraw()  # Hide the main window
    messagebox.showerror(title, message)
    root.destroy()


def validate_message(message: str) -> None:
    """Validate message content and length"""
    if not isinstance(message, str):
        raise ValueError("Message must be a string")
    if not message.strip():
        raise ValueError("Message cannot be empty or whitespace")
    if len(message.encode('utf-8')) > 256:
        raise ValueError("Message exceeds 256 byte limit")


def udp_communicate(
    host: str,
    port: int,
    message: str,
    retries: int = 3,
    timeout: float = 5.0,
    backoff_factor: float = 1.5
) -> Optional[str]:
    """
    Communicate with UDP host with exponential backoff

    Args:
        host: Target host IP or hostname
        port: Target port number
        message: Message to send
        retries: Number of retry attempts
        timeout: Initial timeout in seconds
        backoff_factor: Multiplier for timeout between retries

    Returns:
        Received response or None if all attempts fail
    """
    validate_message(message)
    buffersize = 256
    current_timeout = timeout
    with socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM) as sock:
        for attempt in range(1, retries + 1):
            try:
                # seems one must bind the port to local port as well
                sock.bind(('', port))
                sock.settimeout(current_timeout)
                sock.sendto(message.encode('utf-8'), (host, port))
                # sock.sendto(message.encode(), (host, port))
                logging.info(
                    f"Attempt {attempt}/{retries} sent to {host}:{port}")

                response, addr = sock.recvfrom(buffersize)
                logging.debug(f"Received {len(response)} bytes from {addr}")
                sock.close()
                # print(response.decode('utf-8'))
                return response

            except socket.timeout:
                logging.warning(
                    f"Timeout after {current_timeout}s (attempt {attempt})")
                current_timeout *= backoff_factor
                sock.close()
            except (socket.error, UnicodeDecodeError) as e:
                logging.error(f"Communication error: {str(e)}")
                sock.close()
                break

    logging.error(f"Failed after {retries} attempts")
    return None


def decodeMONIT(input_str: str):
    """Take the received string from MONIT command and decodit it."""
    # Tuples utilized in calculations from T4 Maser Operation Manual
    # See page 47 of said manual
    t4_channel_names = (
        "U batt.A [V]",
        "I batt. A [A]",
        "U batt.B [V]",
        "I batt. B [A]",
        "Set. H [V]",
        "Meas. H [V]",
        "I purifier [A]",
        "I dissociator [A]",
        "H light [V]",
        "IT heater [V]",
        "IB heater [V]",
        "IS heater [V]",
        "UTC heater [V]",
        "ES heater [V]",
        "EB heater [V]",
        "I heater [V]",
        "T heater [V]",
        "Boxes temp. [°C]",
        "I Boxes [A]",
        "Amb. Temp. [°C]",
        "C field [V]",
        "U varactor [V]",
        "U HT ext. [Kv]",
        "I HT ext. [uA]",
        "U HT int. [kV]",
        "I HT int. [uA]",
        "Sto. press. [V]",
        "Sto. heater [V]",
        "Pir. heater [V]",
        "UOCXO 100 MHz [V]",
        "U 405 kHz [V]",
        "U ocxo [V]",
        "+24Vdc [V]",
        "+15Vdc [V]",
        "-15Vdc [V]",
        "+5Vdc [V]",
        "-5Vdc [V]",
        "+8Vdc [V]",
        "+18Vdc [V]",
        "LOCK 100 MHz status",
        "Lock status"

    )

    t4_ADC_fs_counts = (
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        4096,
        256,
        256,
        256,
        256,
        256,
        256,
        256,
        256,
        1
    )

    t4_LSB_Gains = (
        0.02441,
        0.001221,
        0.02441,
        0.001221,
        0.003662,
        0.001221,
        0.001221,
        0.001221,
        0.001221,
        0.004883,
        0.004883,
        0.004883,
        0.004883,
        0.004883,
        0.004883,
        0.004883,
        0.004883,
        0.02441,
        0.001221,
        0.01221,
        float('nan'),
        0.002441,
        0.001221,
        0.1221,
        0.001221,
        0.1221,
        0.004883,
        0.006104,
        0.006104,
        0.002441,
        0.003662,
        0.002441,
        0.09766,
        0.07813,
        -0.07813,
        0.03906,
        float('nan'),
        0.03906,
        float('nan'),
        0.03906,
        1
    )

    # Extract hex data after '$'
    if input_str[0] == 'b' and len(input_str) == 116:
        cleaned_str = input_str[2:-2]  # Remove b' and trailing \r\n
    elif input_str[0] == '$' and len(input_str) == 116:
        cleaned_str = input_str[0:-2]  # Remove \r\n
    else:
        cleaned_str = input_str[0:-2]  # Remove\r\n
    start_index = cleaned_str.find('$') + 1
    hex_data = cleaned_str[start_index:].replace('\\r\\n', '')
    # clean another trailing carriage return
    hex_data = hex_data.replace('\\r\\', '')

    decoded_dict = {}

    try:
        if len(hex_data) == 113:
            # Process channels 0-31 (12 bits = 3 hex characters each)
            for channel in range(32):
                start = channel * 3
                code = hex_data[start:start+3]

                binWd = 4*3
                decimalVal = int(code, 16)
                binspec = '{fill}{align}{width}{type}'.format(
                    fill='0', align='>', width=binWd, type='b')
                binaryVal = format(decimalVal, binspec)
                channelVal = (
                    decimalVal * t4_LSB_Gains[channel]
                )

                decoded_dict[t4_channel_names[channel]] = {
                    'Original_Hex_Code': code,
                    'Binary_Value': binaryVal,
                    'Decimal_Value': decimalVal,
                    'Channel_Value': channelVal

                }

            # Process channels 32-39 (8 bits = 2 hex characters each)
            for idx, channel in enumerate(range(32, 40)):
                start = 96 + (idx * 2)
                code = hex_data[start:start+2]

                binWd = 4*2
                decimalVal = int(code, 16)
                binspec = '{fill}{align}{width}{type}'.format(
                    fill='0', align='>', width=binWd, type='b')
                binaryVal = format(decimalVal, binspec)
                channelVal = (
                    decimalVal * t4_LSB_Gains[channel]
                )

                decoded_dict[t4_channel_names[channel]] = {
                    'Original_Hex_Code': code,
                    'Binary_Value': binaryVal,
                    'Decimal_Value': decimalVal,
                    'Channel_Value': channelVal

                }
            # Process channel 40 (1 bit from first hex character's MSB)
            if len(hex_data) >= 113:
                code = hex_data[112]
                # bit = str((int(code, 16) >> 3) & 0b1)
                channel = 40

                binWd = 4*1
                decimalVal = int(code, 16)
                binspec = '{fill}{align}{width}{type}'.format(
                    fill='0', align='>', width=binWd, type='b')
                binaryVal = format(decimalVal, binspec)
                channelVal = (
                    decimalVal * t4_LSB_Gains[channel]
                )

                decoded_dict[t4_channel_names[channel]] = {
                    'Original_Hex_Code': code,
                    'Binary_Value': binaryVal,
                    'Decimal_Value': decimalVal,
                    'Channel_Value': channelVal

                }

            # print(decoded_dict)

            return decoded_dict

        else:
            raise ValueError("The truncated string returned from the MONIT " +
                             "Command is of the wrong length. It should be " +
                             "equal to 113. The current len(hex_data) " +
                             "is equal " +
                             "to " + str(len(hex_data)) + ".")

    except ValueError as e:
        show_error_dialog("Input Error", str(e))
        logging.error(f"Validation error: {str(e)}")
        sys.exit(1)


def main() -> Optional[str]:
    """Command-line interface entry point."""
    parser = argparse.ArgumentParser(
        description="UDP Client with Retry Logic",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("host", help="Target host IP address")
    parser.add_argument("port", type=int, help="Target port number")
    parser.add_argument("message", help="Message to send")
    parser.add_argument("-r", "--retries", type=int, default=3,
                        help="Number of retry attempts")
    parser.add_argument("-t", "--timeout", type=float, default=5.0,
                        help="Initial timeout in seconds")
    parser.add_argument("-b", "--backoff", type=float, default=1.5,
                        help="Timeout multiplier between attempts")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable debug logging")

    args = parser.parse_args()
    configure_logging(args.verbose)

    try:
        response = udp_communicate(
            args.host,
            args.port,
            args.message,
            args.retries,
            args.timeout,
            args.backoff
        )

        # easy test case
# =============================================================================
#         response = udp_communicate(
#             '10.16.98.16',
#             14000,
#             'MONIT;\r\n',
#             5,
#             2,
#             2
#         )
# =============================================================================

        # Now lets make it human readable
        channelVal_dict = decodeMONIT(response.decode('utf-8'))

        return channelVal_dict

    except ValueError as e:
        logging.error(f"Validation error: {str(e)}")
        sys.exit(1)

    if response:
        print(f"Server response: {response}")
        return channelVal_dict
        sys.exit(0)
    else:
        sys.exit(2)


if __name__ == "__main__":
    main()
