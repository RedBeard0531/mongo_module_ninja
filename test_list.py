#!/usr/bin/env python2

import os
import sys
import json

if len(sys.argv) != 3:
    print(sys.argv[0] + ': out json_list')
    sys.exit(1)

out_file_name = sys.argv[1]
with file(sys.argv[2]) as f:
    list = json.load(f)

contents = '\n'.join(list) + '\n'

# Don't write to the file if it isn't changing
if os.path.exists(out_file_name):
    with file(out_file_name) as f:
        if f.read() == contents:
            sys.exit(0)

with file(out_file_name, 'w') as f:
    f.write(contents)
