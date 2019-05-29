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


def print_warning(err_str):
    """print warning in yellow."""
    print("\033[93mWARNING: %s\033[0m" % (err_str))


def print_ok(err_str):
    """print ok message in green."""
    print("\033[92m%s\033[0m" % (err_str))


def verify_icecream():
    """Do verification"""

    # Step 1 - Check ifconfig for a wired 1 gigabit connection
    ifconfig_str = subprocess.check_output(["ifconfig", "-u"]).decode('utf-8')

    if "100baseT" in ifconfig_str:
        print_warning(
"""Wired ethernet connection is too slow. Icecream requires a 1 Gigabit
ethernet connection to perform well. Verify the wired connection is 1 Gigabit
via "ifconfig", verify the quality of the ethernet cable, and ethernet jack.""")

    if not("1000baseT" in ifconfig_str):
        print_warning(
"""No wired ethernet connection found. Icecream only works with on a
wired network. Make sure a ethernet adaptor is connected. See "Network"
in System Preferences.""")

    # Step 2 - Check for iceccd
    processes_list = subprocess.check_output(["ps", "-axco", "pid,comm"]).decode('utf-8')

    if not "iceccd" in processes_list:
        exit_with_error("iceccd is not running. Please run src/mongo/db/modules/ninja/darwin/setup_icecream.py to run it.")

    if len([ice for ice in processes_list.splitlines() if "iceccd" in ice]) > 1:
        exit_with_error("iceccd is running more then once. Icecream will not work as a result.")

if __name__ == "__main__":
    verify_icecream()
