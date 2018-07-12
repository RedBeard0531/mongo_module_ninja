#! /usr/bin/env python3
"""
Check for computer is in a happy state for evergreen
1. Check for ethernet connection
2. Check iceccd is running
3. Check for magic tarball
"""
import json
import os
import os.path
import string
import subprocess
import sys

def exit_with_error(err_str):
    """print error in red and exit."""
    print("\033[91mERROR: %s\033[0m" % (err_str))
    sys.exit(1)


def exit_with_warning(err_str):
    """print error in red and exit."""
    print("\033[93mWARNING: %s\033[0m" % (err_str))
    sys.exit(0)


def print_ok(err_str):
    """print ok message in green."""
    print("\033[92m%s\033[0m" % (err_str))


def verify_icecream():
    """Do verification"""

    # Step 1 - Check for ethernet
    ethernet_ports_str = subprocess.check_output(["networksetup", "listallhardwareports"]).decode('utf-8')
    ethernet_ports = ethernet_ports_str.splitlines()
    port_count = int((ethernet_ports.index('VLAN Configurations') - 1) / 4)
    found_ethernet = False
    for i in range(port_count):
        base = i * 4
        hw_port = ethernet_ports[base + 1]
        device = ethernet_ports[base + 2]
        eth_address = ethernet_ports[base + 3]
        device = device.replace("Device: ", "")

        #print ("%s - %s - %s" % (hw_port, device, eth_address))

        # Well known adaptors
        # May be nice to check for speed
        if not("Thunderbolt Ethernet" in hw_port or "USB 10/100/1000 LAN" in hw_port):
            continue

        ifconfig = subprocess.check_output(["ifconfig", device]).decode('utf-8')

        # There is always a space after "inet". We check that the device gets an address
        # We don't check the routing rules since mac promotes the wired device over wifi
        if not("inet " in ifconfig and "inet6" in ifconfig):
            continue

        found_ethernet = True

    if not found_ethernet:
        exit_with_warning(
"""No wired ethernet connection found. Icecream only works with on a
wired network. Make sure a ethernet adaptor is connected. See "Network"
in System Preferences.""")

    # Step 2 - Check for iceccd
    processes_list = subprocess.check_output(["ps", "-axco", "pid,comm"]).decode('utf-8')

    if not "iceccd" in processes_list:
        exit_with_warning("iceccd is not run. Please run src/mongo/db/modules/ninja/setup_icecream.py to run it.")

    if len([ice for ice in processes_list.splitlines() if "iceccd" in ice]) > 1:
        exit_with_error("iceccd is running more then once. Icecream will not work as a result.")

    # Step 3 - Check for magic tarball
    if os.path.exists("build") and not os.path.exists("build/a91375a9328f2a515182caf1be3e2f2c.tar.gz"):
        exit_with_error("""Linux toolchain tarball not found.
Download linux tarball from here via Google Chrome (or any browser you are signed into your 10gen google account with):
https://drive.google.com/file/d/1OaoPq_wEfW5sJsc3aRwQMc0gBULIbRBp/view?usp=sharing
and call it `a91375a9328f2a515182caf1be3e2f2c.tar.gz`. Put it in the `build` directory in your mongo repo.
""")

if __name__ == "__main__":
    verify_icecream()
