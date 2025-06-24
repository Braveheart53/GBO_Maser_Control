import T4_Maser_Comms as T4M

monitstr = T4M.udp_communicate('10.16.98.16', 14000, 'MONIT;\r\n',
                               5, 2, 2)
# print(monitstr)

Channel_dict = T4M.decodeMONIT(monitstr.decode('utf-8'), True)
print(Channel_dict)
