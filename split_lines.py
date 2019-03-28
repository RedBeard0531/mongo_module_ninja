# Converts an RSP file intended to MSVC linker into one argument per line.

import shlex
import sys

if len(sys.argv) != 2:
    print(sys.argv[0] + ': rsp_file')
    sys.exit(1)

with open(sys.argv[1]) as f:
    lines = [arg + '\n' for arg in shlex.split(f.read(), posix=False)]

with open(sys.argv[1], 'w') as f:
    f.writelines(lines)
