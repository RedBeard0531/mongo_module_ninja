import sys
import os
import math

def createIfNeeded(path):
    if not os.path.exists(path):
        if not os.path.exists(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))
        open(path, 'w').close()

def run_if_needed(base_file, then_file, now_file):
    # Python uses doubles for mtime so it can't precisely represent linux's
    # nanosecond precision. Round up to next whole second to ensure we get a
    # stable timestamp that is guaranteed to be >= the timestamp of the
    # compiler. This also avoids issues if the compiler is on a file system
    # with high-precision timestamps, but the build directory isn't.
    base_stat = os.stat(base_file)
    mtime = math.ceil(base_stat.st_mtime)
    atime = math.ceil(base_stat.st_atime)

    if (os.path.exists(then_file)
            and os.path.exists(now_file)
            and os.stat(then_file).st_mtime == mtime):
        return # Don't need to do anything.

    createIfNeeded(now_file)
    os.utime(now_file, None) # None means now

    createIfNeeded(then_file)
    os.utime(then_file, (atime, mtime))

if __name__ == '__main__':
    if len(sys.argv) != 4:
        print((sys.argv[0] + ': base_file then_file now_file'))
        sys.exit(1)

    base_file = sys.argv[1]
    then_file = sys.argv[2]
    now_file = sys.argv[3]
    run_if_needed(base_file, then_file, now_file)

