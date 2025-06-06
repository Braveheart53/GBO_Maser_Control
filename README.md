# 1 General GBO_Maser_Control
Scripts and methods utilized to communicate and control various masers at GBO with Python 3.8

# 2 Repository Organization
This repository will have a project created for each maser utilized at GBO with python control. It was initiated on 2025-06-05. All revision schemas will 
follow [semantic versioning / revisioning scheme ](<https://semver.org/>) with a slight alteration as follows:

Where the semantic string is X.Y.Z, the following applies as definitions:
Z == work in progress or internal drafts, only reviewed / edited by current developers
Y == releases for internal development or review
X == external releases and releases ready for production use

# 3 Tasks

## 3.1 T4 i3000 Maser
As of 2025-06-05 this is the only maser with scripts in the repository (repo).
- [OEM: Safran Group](<https://safran-navigation-timing.com/>)
- [Product Page](<https://safran-navigation-timing.com/product/imaser-3000/>)

### 3.1.1 Scrips and how to use them
- T4_Maser_Comms_1p0p0.py
  - script to communicate and pass commands via UDP on inrtanet for site control
  - can be imported or run on the command line, an example is given within the scrip header
- T4_Comms_Import.py
  - import T4_Maser_Comms current version to create a human readable return of the MONIT command.
- T4_fromOEM.py
  - provided by Safran Group / iScience as an example for UDP Comms

## 3.2 Microsemi / Michrochip Maser

# 4 Other Timing Related Scripts and Repos
