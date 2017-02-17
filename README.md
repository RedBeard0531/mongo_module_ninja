# MongoDB ninja module

This is a module for mongodb that makes scons generate build.ninja files.

**☠️ WARNING: This is still experimental. Use at your own risk. ☠️**

To use it, check out this repo into the `src/mongo/db/modules` directory in
your mongodb checkout. You may want to rename it to something short like
`ninja`.  Then run `scons` with your favorite flags and have it build
build.ninja. You can now use `ninja` to build anything that scons can.

```bash
cd src/mongo/db/modules
git clone https://github.com/RedBeard0531/mongo_module_ninja ninja
cd -

# On non-linux, remove --link-model=static.
# Also, read the section about split DWARF below.
scons --link-model=static CC=clang CXX=clang++ \
    CCFLAGS='-Wa,--compress-debug-sections -gsplit-dwarf' \
    MONGO_VERSION='0.0.0' MONGO_GIT_HASH='unknown' \
    VARIANT_DIR=ninja --modules=ninja \
    build.ninja

export NINJA_STATUS='[%f/%t (%p) %es] ' # make the ninja output even nicer

ninja mongod # builds mongod
ninja # builds the defualt target (still mongod)
ninja core # supports all scons aliases except lint and distsrc
ninja build/unittests/TAB # autocompletion should work
```

If you want to change your build flags, just run the `scons` command with the
new flags to have it regenerate the build.ninja file. `ninja` will
automatically regenerate the build.ninja file whenever any of the SCons files
change so you don't shouldn't need to manually rerun scons often.

This requires ninja >= 1.7. You can download it from
[here](https://github.com/ninja-build/ninja/releases)
if if isn't in your distribution. Note that Fedora calls both the binary and the
package `ninja-build`. Ubuntu calls the package `ninja-build` but leaves the
binary named `ninja`. Ubuntu <= yakkety (16.10) uses an old version of ninja so
you will need to download the binary if you aren't running that release.

## ccache support

If you have `ccache` installed and on your path, it will be used automatically.
If you have it installed but don't want to use it, pass `--no-cache` to scons.

You can tell if it is being used by the message printed by scons:

```
> scons --modules=ninja build.ninja
...
Generating build.ninja with ccache support (pass --no-cache to scons to disable)
scons: done building targets.

> scons --modules=ninja build.ninja --no-cache
...
Generating build.ninja
scons: done building targets.
```

## Multiple .ninja files

If you often switch between multiple sets of flags, you can make a `*.ninja`
file for each set. Each `*.ninja` file is executable so you can run it directly,
but unfortunately that breaks tab completion.

I suggest passing `--config=force` to `scons` for all of your `*.ninja` files to
keep scons from getting confused as you switch.  If you are using ccache, I
suggest using the `VARIANT_DIR=ninja` scons variable so that all builds have the
same path. Conversely, if you don't use ccache, I suggest using a different
`VARIANT_DIR` for each set of flags so they don't conflict.

```bash
scons CC=clang CXX=clang++ VARIANT_DIR=ninja --config=force build.ninja
scons CC=gcc CXX=g++ VARIANT_DIR=ninja --config=force gcc.ninja

ninja mongod # builds mongod with clang
ninja -f gcc.ninja mongod # builds mongod with gcc
./gcc.ninja mongod # shorter syntax
```

## Split DWARF info

**☠️ WARNING: This is even more experimental than everything else! ☠️**

You can pass `CCFLAGS=-gsplit-dwarf` to try out split dwarf support which makes
linking much faster. `ccache` >= 3.2.3 supports it out of the box so they can be
used together. `scons` will error if you use -gsplit-dwarf on an older ccache.

On macOS this requires Xcode >= 8. On linux this requires all supported compiler
versions work. `scons` will not error if you don't have a new enough compiler,
but you can check for yourself by running `find build/ -name *.dwo` after
compiling and seeing if there are any files.

Additionally, in order to actually *use* the dwarf info, your debugging tools
will need to support it. I've tested the latest gdb, perf, addr2line, and
llvm-symbolizer (used by `mongosymb.py`) on linux and they all work. I don't know
about other platforms or older versions. If your tool of choice doesn't work,
upgrade or remove `-gsplit-dwarf` and recompile.

<!-- vim: set tw=80 : -->
