#!/usr/bin/env python2

import os
import subprocess
import sys
import re
import json

if len(sys.argv) != 4:
    print sys.argv[0] + ': in out json_subs'
    sys.exit(1)

with file(sys.argv[1]) as f:
    contents = f.read()
out_file_name = sys.argv[2]
with file(sys.argv[3]) as f:
    vars = json.load(f)
    subs = vars['subs']
    do_chmod = vars['do_chmod']

if isinstance(subs, dict):
    subs = subs.items()

for k,v in subs:
    # scons Substfile just defers to re.sub
    contents = re.sub(k, str(v), contents)

# Don't write to the file if it isn't changing
if os.path.exists(out_file_name) and not do_chmod:
    with file(out_file_name) as f:
        if f.read() == contents:
            sys.exit(0)

with file(out_file_name, 'w') as f:
    f.write(contents)

if do_chmod:
    subprocess.check_call(['chmod', 'oug+x', out_file_name])
