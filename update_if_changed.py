import sys
import os

in_file = sys.stdin.read()
out_file_name = sys.argv[1]

if os.path.exists(out_file_name):
    with open(out_file_name, 'r') as f:
        if f.read() == in_file:
            sys.exit(0)

with open(out_file_name, 'w') as f:
    f.write(in_file)

