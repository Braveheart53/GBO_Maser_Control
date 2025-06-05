# -*- coding: utf-8 -*-
"""
=============================================================================
# %% Header Info
--------

Created on %(date)s

# %%% Author Information
@author: William W. Wallace
Author Email: wwallace@nrao.edu
Author Secondary Email: naval.antennas@gmail.com
Author Business Phone: +1 (304) 456-2216


# %%% Revisions
--------
Utilizing Semantic Schema as External Release.Internal Release.Working version

# %%%% 0.0.1: Script to run in consol description
Date: 2025-06-04
# %%%%% Function Descriptions
        main: main script body
        select_file: utilzing module os, select multiple files for processing

# %%%%% Variable Descriptions
    Define all utilized variables
        file_path: path(s) to selected files for processing

# %%%%% More Info
    Taken direcly from script from iScience / Safran group
# %%%% 0.0.2: NaN
Date: 
# %%%%% Function Descriptions
        main: main script body
        select_file: utilzing module os, select multiple files for processing
    More Info:
# %%%%% Variable Descriptions
    Define all utilized variables
        file_path: path(s) to selected files for processing
# %%%%% More Info
=============================================================================
"""
import socket

serverAddress = '10.16.98.16'
serverPort = 14000
UDP_timeout = 2
bufferSize = 256

cmd = 'MONIT;\r\n'

UDPClientSocket = socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM)
UDPClientSocket.bind(('', 14000))
UDPClientSocket.settimeout(UDP_timeout)

UDPClientSocket.sendto(cmd.encode(), (serverAddress, serverPort))
msgRAW = UDPClientSocket.recvfrom(bufferSize)
UDPClientSocket.close()
print(msgRAW[0])
