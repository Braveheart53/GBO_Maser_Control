import ab_meter_caller as abc

data = abc.run(count=3, interval=30)
# data lives here, for example L1 Voltage value
data["10.16.130.50"]["Real_Time_Power_Table"]["columns"]["Total Real Power"]
