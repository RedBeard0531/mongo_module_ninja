#! /usr/bin/env python3
"""
Check for icecream is installed
1. Check for homebrew
2. Check what version is install
3. Find the version
4. Load the plist file into launchctl
"""
import json
import os
import os.path
import string
import subprocess
import sys

PLIST_FILE_TEMPLATE = """
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.mongodb.iceccd</string>
    <key>ProgramArguments</key>
    <array>
    <string>/usr/local/Cellar/icecream/${ICE_VERSION}/sbin/iceccd</string>
    <string>--no-remote</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardErrorPath</key>
    <string>${ICE_STDERR}</string>
    <key>StandardOutPath</key>
    <string>${ICE_STDOUT}</string>
</dict>
</plist>
"""

def exit_with_error(err_str):
    """print error in red and exit."""
    print("\033[91mERROR: %s\033[0m" % (err_str))
    sys.exit(1)

def print_ok(err_str):
    """print ok message in green."""
    print("\033[92m%s\033[0m" % (err_str))


def setup_icecream():
    """Do setup"""

    brew_cmd = "/usr/local/bin/brew"

    # Step 1 - check for brew
    if not os.path.exists(brew_cmd):
        exit_with_error("Homebrew was not found at %s, please install homebrew at http://brew.sh" % (brew_cmd))

    # Step 2 - check version of icecream
    try:
        icecream_js = subprocess.check_output([brew_cmd, "info", "--json=v1", "icecream"]).decode('utf-8')
    except subprocess.CalledProcessError:
        exit_with_error("icecream is not installed, please install icecream with 'brew install icecream'")

    # Step 3 - Find the version
    icecc_js = json.loads(icecream_js)

    # Step 3.1 - Try grabbing linked_key first. 
    # If icecream is not linked, it comes back as "null" from brew, without the quotes
    icecream_version =  icecc_js[0]["linked_keg"]

    if not icecream_version:
        exit_with_error("The homebrew version of icecream is not linked, run 'brew link icecream'")

    # Step 4 - Install plist file
    # Use ~/.local/share/icecream for log files
    ice_log_path = os.path.join(os.environ["HOME"], ".local/share/icecream")

    if not os.path.exists(ice_log_path):
        os.makedirs(ice_log_path)

    plist = string.Template(PLIST_FILE_TEMPLATE).substitute(ICE_VERSION=icecream_version,
        ICE_STDERR = os.path.join(ice_log_path, "stderr.log"),
        ICE_STDOUT = os.path.join(ice_log_path, "stdout.log"))
 
    plist_path = os.path.join(os.environ["HOME"], "Library/LaunchAgents/com.mongodb.iceccd.plist")

    with open(plist_path, 'w') as fh:
        fh.write(plist)

    # Step 4 - Reload the file into launchctl

    # This will not fail when it does not exist but we hide the output to avoid confusing the user
    try:
        subprocess.check_output(['launchctl', 'unload', '-w', plist_path], stderr=subprocess.STDOUT).decode('utf-8')
    except subprocess.CalledProcessError:
        exit_with_error("Launchctl failed. Run launchctl unload -w %s to investigate" % (plist_path))

    subprocess.check_call(['launchctl', 'load', '-w', plist_path])
    
    print_ok("Icecream setup with launchctl complete. Happy Building!")
    print("\nIcecream will now be run as the current user on login.")
    print("To stop, run launchctl stop com.mongodb.iceccd")
    print("To disable, run launchctl unload -w %s" % (plist_path))
    return

if __name__ == "__main__":
    setup_icecream()
