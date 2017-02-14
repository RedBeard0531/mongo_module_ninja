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

scons --modules=ninja CC=clang CXX=clang++ --link-model=static build.ninja

export NINJA_STATUS='[%f/%t (%p) %es] ' # make the ninja output even nicer
ninja mongod # builds mongod
ninja # builds the defualt target (still mongod)
ninja core # also supports aliases
ninja build/unittests.txt # calls back into scons automatically for some files
ninja build/unittests/TAB # autocompletion should work
```

If you want to change your build flags, just run the `scons` command with the
new flags to have it regenerate the build.ninja file. `ninja` will
automatically regenerate the build.ninja file whenever any of the SCons files
change so you don't shouldn't need to manually rerun scons often.

## ccache support

If you have `ccache` installed and on your path, it will be used automatically.
If you have it installed but don't want to use it pass `--no-cache` to scons.

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
linking much faster. `ccache` already supports it out of the box so they can be
used together.

## Windows support
Pull requests are welcome. It shouldn't be too hard. I think most changes will
need to be in the `NinjaFile.write_rules` method.

<!-- vim: set tw=80 : -->
