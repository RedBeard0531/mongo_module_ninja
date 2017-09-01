# MongoDB ninja module

This is a module for mongodb that makes scons generate build.ninja files.

**☠️ WARNING: This is still experimental. Use at your own risk. ☠️**

To use it, check out this repo into the `src/mongo/db/modules` directory in
your mongodb checkout. You may want to rename it to something short like
`ninja`.  Then run `scons` with your favorite flags and have it build
build.ninja. You can now use `ninja` to build anything that scons can.

```bash
mkdir -p src/mongo/db/modules
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
| `--icecream` | off | **LINUX ONLY** Use [icecream](#-icecream-support) for distributed compilation |
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
1. If you are using icecream and you build seems to hang or go really slowly,
   try restarting the icecream daemon. `systemctl restart iceccd` or
   `systemctl restart icecream` depending on which distribution you are using.

## Building and running unit tests

You can run `ninja +name_of_test` to build then run a cpp unit test. This uses
the "basename" of the test, so `build/ninja/mongo/bson/bson_obj_test` is just
`ninja +bson_obj_test`. This is intended to simplify iterating on one or two
tests. To run all of the unittests, continue to use something like `ninja
unittests && buildscripts/resmoke.py --sute=unittests -j16`.

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

## 🍨 Icecream support

On linux, you can use [icecream](https://github.com/icecc/icecream) to
distribute your compile tasks to your neighbors' computers, and literally Build
Together. This can dramatically reduce the time to do large rebuilds.

1. Make sure you are using [ccache](#ccache-support)
1. Follow the distribution-specific steps below to install and run the icecream
   daemon
1. Add `--icecream` to the list of flags you pass to scons when building your
   `build.ninja` file
1. Run `ninja` with a high `-j` value such as `-j400` (this is specifically for
   when running ninja since the `-j` you pass to scons when building the
   `build.ninja` file doesn't matter)

Since others can now schedule builds on your machine at any time, consider
disabling the icecream daemon when doing benchmarking. Depending on your
distribution, this is either `systemctl stop icecream` or `systemctl stop
iceccd`. You will want to restart the daemon before compiling again.

Until [this issue](https://github.com/ccache/ccache/issues/185) is resolved,
ccache requires an additional pass of the C++ preprocessor. This can become a
bottleneck limiting the speed that you can submit jobs to the cluster. You can
set the `CCACHE_DISABLE=1` environment variable when running ninja to speed up
your builds with the trade-off that it won't cache the compilations.

### Installing icecream on Ubuntu (and similar distros)

1. `apt-get install icecc`
1. Download the `amd64.deb` from
   [this ppa](http://ppa.launchpad.net/t-oss/icecc-beta/ubuntu/pool/main/i/icecc/)
   and install it with `dpkg -i` (yes you need to install from the main repo
   first then upgrade...)

### Installing icecream on ArchLinux

1. Install `icecream` from the [AUR](https://aur.archlinux.org/packages/icecream/)
1. `systemctl enable --now icecream.service`

### Installing icecream on Fedora

1. `dnf install icecream` and make sure it is installing 1.1rc2
1. `firewall-cmd --zone=FedoraServer --add-service=icecream`
1. `firewall-cmd --permanent --zone=FedoraServer --add-service=icecream`

<!-- vim: set tw=80 : -->
