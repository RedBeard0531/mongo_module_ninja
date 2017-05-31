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

# On non-linux, remove -gsplit-dwarf.
# Also, be sure to read the section about split DWARF below.
python buildscripts/scons.py CC=clang CXX=clang++ \
    CCFLAGS='-Wa,--compress-debug-sections -gsplit-dwarf' \
    MONGO_VERSION='0.0.0' MONGO_GIT_HASH='unknown' \
    VARIANT_DIR=ninja --modules=ninja \
    build.ninja

export NINJA_STATUS='[%f/%t (%p) %es] ' # make the ninja output even nicer

ninja mongod # builds mongod
ninja # builds the default target (still mongod)
ninja core # supports all scons aliases except lint and distsrc
ninja build/unittests/TAB # autocompletion should work
```

If you want to change your build flags, just run the `scons` command with the
new flags to have it regenerate the build.ninja file. `ninja` will
automatically regenerate the build.ninja file whenever any of the SCons files
change so you don't shouldn't need to manually rerun scons often.

This module requires ninja >= 1.7. You can download it from
[here](https://github.com/ninja-build/ninja/releases)
if if isn't in your distribution. Note that Fedora calls both the binary and the
package `ninja-build`. Ubuntu calls the package `ninja-build` but leaves the
binary named `ninja`. Ubuntu <= yakkety (16.10) uses an old version of ninja so
you will need to download the binary if you aren't running that release.

## New scons options

This module adds the following options to scons. Unfortunately, they won't show
up with `--help` so they are documented here.

| Flag | Default | Description |
| ---- | ------- | ----------- |
| `--link-pool-depth=NNN` | 4 | **WINDOWS ONLY**: limit the number of concurrent link tasks |
| `--ninja-builddir=path` | current directory | Where ninja stores [its database](https://ninja-build.org/manual.html#ref_log). **Delete your `build/` directory if you change this!** |

## Troubleshooting

1. Email or slack me (Mathias) if you run in to any problems.
1. If scons says your C or C++ compiler doesn't work, pass the `--config=force`
   flag to scons.
1. If you get an error about `is_derived_node` you are using an old version of
   scons. Try using `python buildscripts/scons.py` rather than just `scons`.
1. If you get an error about `Unknown variables specified:` try removing all of
   the `\`s from your command line.
1. If scons is prompting for your password, try checking out this module with
   the `https://` url used above rather than a `git@github.com` url.
1. If any of your debugging tools behave oddly, read the section about split
   DWARF info below and consider removing `-gsplit-dwarf` from your CCFLAGS.
1. If ccache doesn't seem to be working, run `CCACHE_LOGFILE=/tmp/ccache.log
   ninja -j1`, let it compile a few objects, then look at
   `/tmp/ccache.log`. It should tell you why it isn't able to use the cache. If
   that doesn't help, see step 1.

## Building and running unit tests

You can run `ninja +name_of_test` to build then run a cpp unit test. This uses
the "basename" of the test, so `build/ninja/mongo/bson/bson_obj_test` is just
`ninja +bson_obj_test`. This is intended to simplify iterating on one or two
tests. To run all of the unittests, continue to use something like `ninja
unittests && buildscripts/resmoke.py --suites=unittests -j16`.

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

## Using ninja to generate a compiledb (compile_commands.json)

You can have ninja generate the compilation db used by many clang-based tools by
running `ninja compile_commands.json` or using the `compiledb` alias like in
scons. For your convienience this will also update all generated sources so
tools will work when a compile db is created on a clean build tree. You probably
only want to use this with a .ninja file configured to use clang so that it uses
the set of flags that most tools expect.

The compilation db will be slightly different than the one generated by scons.
It adds flags that ninja uses to track header dependencies and each command may
be prefixed by `ccache`. I have tested this with
[rtags](https://github.com/Andersbakken/rtags),
[YouCompleteMe/ycmd](https://valloric.github.io/YouCompleteMe/) and a few of the
[extra clang tools](http://clang.llvm.org/extra/) and they all handle this fine.
Please let me know if this causes problems for any tools you use.

## Split DWARF info

**☠️ WARNING: This is even more experimental than everything else! ☠️**

On linux, you can pass `CCFLAGS=-gsplit-dwarf` to try out split dwarf support
which makes linking much faster. `ccache` >= 3.2.3 supports it out of the box so
they can be used together. `scons` will error if you use -gsplit-dwarf with an
older ccache or an unsupported platform.

In order to actually *use* the dwarf info, your debugging tools will need to
support it. I've tested the latest perf, addr2line, and llvm-symbolizer
(used by `mongosymb.py`) on linux and they all work. I don't know about older
versions or other tools. If your tool of choice doesn't work, upgrade or remove
`-gsplit-dwarf` and recompile.

GDB >= 7.11 has a [bug](https://sourceware.org/bugzilla/show_bug.cgi?id=20899)
that makes it show all namespaces other than `std` as `(anonymous namespace)`.
If this affects you, you can either recompile without `-gsplit-dwarf` or apply
the patch from that ticket to your gdb. If you are a MongoDB employee, you can
download the latest version of
[our toolchain](https://evergreen.mongodb.com/waterfall/toolchain-builder) which
includes a patched gdb.

<!-- vim: set tw=80 : -->
