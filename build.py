from __future__ import print_function

import SCons
from SCons.Script import *
from SCons.Util import flatten

import io
import os
import re
import sys
import glob
import json
import shlex
import fnmatch
import requests
import subprocess
import multiprocessing
from buildscripts import errorcodes


my_dir = os.path.dirname(__file__)
def ospath(file):
    return file.replace('/',os.path.sep)

def sibling(*file):
    return os.path.normpath(os.path.join(my_dir, *file))

try:
    import ninja_syntax
    import touch_compiler_timestamps
except ImportError:
    # Sometimes we can't import a sibling file. This makes it possible.
    sys.path.append(my_dir)
    import ninja_syntax
    import touch_compiler_timestamps

AddOption('--link-pool-depth',
        default=4,
        type='int',
        action='store',
        dest='link-pool-depth',
        help='WINDOWS ONLY: limit of concurrent links (default 4)')

AddOption('--ninja-builddir',
        type='str',
        action='store',
        dest='ninja_builddir',
        help="Set the location of ninja's builddir for the .ninja_log and .ninja_deps files"
             " (default is current directory)")

AddOption('--icecream',
        default=False,
        action='store_true',
        dest='icecream',
        help='Use the icecream distributed compile server')

AddOption('--pch',
        default=False,
        action='store_true',
        dest='pch',
        help='Use pre-compiled headers, incompatible with icecream')

split_lines_script = os.path.join(my_dir, 'split_lines.py')
subst_file_script = os.path.join(my_dir, 'subst_file.py')
test_list_script = os.path.join(my_dir, 'test_list.py')
touch_compiler_timestamps_script = os.path.join(my_dir, 'touch_compiler_timestamps.py')

verify_icecream_script = os.path.join(my_dir, 'darwin', 'verify_icecream.py')

icecc_create_env = os.path.join(my_dir, 'icecream', 'icecc-create-env')

def makeNinjaFile(target, source, env):
    assert not source
    ninja_file = NinjaFile(str(target[0]), env)
    ninja_file.write()

def rglob(pattern, root='.') :
    return [os.path.join(path, f)
            for path, dirs, files in os.walk(root, followlinks=True)
            for f in fnmatch.filter(files, pattern)]

def where_is(env, exe):
    path = env.WhereIs(exe)
    if not path:
        # check a few places that are often on $PATH but scons hides.
        if os.path.exists('/usr/local/bin/'+exe):
            path = '/usr/local/bin/'+exe
        elif os.path.exists(os.path.expanduser('~/bin/')+exe):
            path = os.path.expanduser('~/bin/')+exe
        elif os.path.exists('/opt/local/bin/'+exe):
            path = '/opt/local/bin/'+exe
        elif os.path.exists('/usr/lib/icecream/bin/'+exe):
            path = '/usr/lib/icecream/bin/'+exe

    # Normalize missing to '' rather than None
    return path if path else ''

def strmap(node_list):
    for node in node_list:
        assert isinstance(node, (str, SCons.Node.FS.Base, SCons.Node.Alias.Alias))
    return [str(node) for node in node_list]

def fetch_icecream_tarball():
    LINK_URL = 'http://mongodbtoolchain.build.10gen.cc/icecream/ubuntu1604/x86_64/latest'
    NAME_FILE = 'build/icecc_envs/latest'
    os.makedirs('build/icecc_envs', exist_ok = True)

    response = requests.head(LINK_URL, allow_redirects=True)
    if not response.ok:
        if os.path.exists(NAME_FILE):
            with open(NAME_FILE) as f:
                local_file = f.read()
                print("Can't fetch {}, assuming {} is up to date".format(LINK_URL, local_file))
                return local_file
        else:
            print("error fetching url for latest icecream env: " + str(response))
            Exit(1)

    url = response.url
    size = int(response.headers['Content-length'])

    remote_file = url.split('/')[-1]
    local_file = os.path.join('build', 'icecc_envs', remote_file)
    if (os.path.exists(NAME_FILE)
            and os.path.exists(local_file)
            and os.stat(local_file).st_size == size):
        with open(NAME_FILE) as f:
            if f.read() == remote_file:
                print("{} up to date".format(local_file))
                return local_file

    print("fetching {} ({}MB)".format(url, size // (1024*1024)))
    response = requests.get(url)
    if not response.ok:
        print("error fetching latest icecream env: " + str(response))
        Exit(1)
    with open(local_file, "wb") as f:
        f.write(response.content)

    with open(NAME_FILE, 'w') as f:
        f.write(local_file)

    return local_file

class NinjaFile(object):
    def __init__(self, name, env):
        self.ninja_file = name
        self.globalEnv = env
        self.aliases = {}
        self.vars = {}
        self.overrides = {}
        self.tool_commands = {}
        self.tool_paths = set()
        self.builds = []
        self.built_targets = set()
        self.generated_headers = set()
        self.rc_files = []
        self.unittest_shortcuts = {}
        self.unittest_skipped_shortcuts = set()

        self.init_idl_dependencies()
        self.find_build_nodes()
        self.find_aliases()
        self.add_run_test_builds()
        self.set_up_complier_upgrade_check()

        if env.get('_NINJA_USE_ERRCODE'):
            self.add_error_code_check()
        if env.get('_NINJA_CCACHE'):
            self.set_up_ccache()
        if env.get('_NINJA_ICECC'):
            if env.TargetOSIs('darwin'):
                self.add_icecream_check()
            self.set_up_icecc()

        if GetOption('pch'):
            self.enable_pch()

        self.hide_slow_compile_latency()

        assert 'COPY' not in self.vars
        if self.globalEnv.TargetOSIs('windows'):
            self.vars['COPY'] = 'cmd /c copy'
        else:
            self.vars['COPY'] = 'install' # install seems faster than cp.

        assert 'PYTHON' not in self.vars
        self.vars['PYTHON'] = self.globalEnv.WhereIs('$PYTHON')

    def init_idl_dependencies(self):
        # The IDL files depend on the python scripts so get a list of IDL related python files.
        # This is done by idl_tool.py but we need to duplicate the logic since we do not run
        # the scanner. We get the list once and cache it.
        self.idl_deps = glob.glob('buildscripts/idl/*.py')
        self.idl_deps.extend(glob.glob('buildscripts/idl/idl/*.py'))

    def enable_pch(self):
        assert not self.globalEnv.get('_NINJA_CCACHE')

        pch_dir = ospath('build/%s/mongo/'%self.globalEnv.subst('$VARIANT_DIR'))
        # Prefer CXX on MSVC since MSVC always has a SHCXX due to the MSI custom action dll.
        pch_tool = ('SHCXX'
                    if 'SHCXX' in self.tool_commands and not self.globalEnv.ToolchainIs('msvc')
                    else 'CXX')
        pchvars = {}

        for build in self.builds:
            if build['rule'] in ('CXX', 'SHCXX'):
                if build['inputs'][0].startswith(ospath('src/mongo')):
                    if build['inputs'][0] == ospath('src/mongo/base/system_error.cpp'):
                        # HACK: this happens to be a good file to base the pch flags off of.
                        # It needs to be in an lib in an env that hasn't had too much injection.
                        pchvars = dict(**build['variables'])

                    is_test = 'test' in build['inputs'][0]
                    if ((is_test and build['rule'] == 'SHCXX')
                            or (not is_test and build['rule'] != pch_tool)):
                        continue # no pch for this file.

                    pch_file = 'test-pch.h' if is_test else 'pch.h'
                    if not self.globalEnv.ToolchainIs('msvc'):
                        # -include uses path to file
                        build['variables']['pch_flags'] = '-include ' + pch_dir + pch_file
                        build.setdefault('implicit', []).append(pch_dir + pch_file + '.$pch_suffix')
                    else:
                        # /FI and friends use the same rules for paths as #include
                        build['variables']['pch_flags'] = (
                                '/Fp{0}{1}.$pch_suffix /Yumongo/{1} /FImongo/{1}'
                                    .format(pch_dir, pch_file))
                        # Ninja only knows about the .obj file and uses that, not the .pch file, to
                        # track header dependencies. This works around the ninja limitation that
                        # rules using 'deps' can't have builds with multiple outputs.
                        build.setdefault('implicit', []).append(pch_dir + pch_file + '.obj')

            elif build['rule'] == 'LINK' and self.globalEnv.ToolchainIs('msvc'):
                build.setdefault('inputs', []).extend([pch_dir+'pch.h.obj',
                                                       pch_dir+'test-pch.h.obj'])

        self.vars['pch_flags'] = ''
        self.vars['pch_suffix'] = 'gch' if self.globalEnv.ToolchainIs('gcc') else 'pch'

        for tool in [t for t in ('CXX', 'SHCXX') if t in self.tool_commands]:
            if not self.globalEnv.ToolchainIs('msvc'):
                # position matters on non-msvc compilers
                self.tool_commands[tool] = self.tool_commands[tool].replace(
                    '$out',
                    '$out $pch_flags')
            else:
                self.tool_commands[pch_tool] += ' $pch_flags'

        for (pch_file, rule) in (('pch.h', pch_tool), ('test-pch.h', 'CXX')):
            # Copy the pch headers to the build dir so the compiled pch is there rather than in the
            # source tree. They need to be in the same directory.
            self.builds.append(dict(
                rule='INSTALL',
                inputs=sibling(pch_file),
                outputs=pch_dir + pch_file))

            pchvars['description']= 'PCH_{} {}.$pch_suffix'.format(rule, pch_file)
            if not self.globalEnv.ToolchainIs('msvc'):
                pchvars['pch_flags']= '-x c++-header'
                self.builds.append(dict(
                    rule=rule,
                    inputs=pch_dir + pch_file,
                    outputs=pch_dir + pch_file + '.$pch_suffix',
                    order_only='_generated_headers',
                    variables=pchvars,
                    ))
            else:
                pchvars['_MSVC_OUTPUT_FLAG'] = '/Fo%s%s.obj'%(pch_dir, pch_file)
                pchvars['pch_flags'] = '/Fp{0} /Yc{1} /FI{1}'.format(pch_dir + pch_file + '.pch',
                                                                'mongo/' + pch_file)
                self.builds.append(dict(
                    rule=rule,
                    inputs=pch_dir + pch_file,
                    outputs=pch_dir + pch_file + '.obj',
                    order_only='_generated_headers',
                    variables=dict(pchvars), # copy it
                    # can't have multiple outputs.
                    #implicit_outputs=pch_dir + pch_file + '.pch',
                    ))

    def add_run_test_builds(self):
        # For everything that gets installed to build/unittests, add a rule for +basename
        # that runs the test from its original location.
        paths = (
            # Not including build/integration_tests since they need a server to run.
            os.path.join('build', 'unittests'),
            os.path.join('build', 'benchmark'),
        )

        # If we translated the newer scons generated @ rules to + rules, do not add duplicates
        for build in self.builds:
            for output in build['outputs']:
                if output.startswith('+'):
                    return

        def is_test_like(name):
            return any(name.startswith(path) for path in paths)
        tests = [flatten(build['inputs'])[0]
                 for build in self.builds
                 if build['rule'] == 'INSTALL'
                 and is_test_like(flatten(build['outputs'])[0])]
        self.builds += [dict(outputs='+'+os.path.basename(test), inputs=test, rule='RUN_TEST')
                        for test in tests]

    def set_up_complier_upgrade_check(self):
        # This is based on a suggestion from the ninja mailing list. It creates two files, a
        # then_file with the mtime of the compiler and a now_file with an mtime of the last time
        # this task runs. This task depends on the compiler so that if it gets upgraded it will be
        # newer than the then_file so this task will rerun and update both files. All compiles and
        # the configure step depend on the now_file, so they get rerun whenever it is updated. This
        # is all to work around the fact that package managers back-date the mtimes when installing
        # to the time is was build rather than the time it was installed, so just depending on the
        # compiler itself doesn't actually work.
        cxx = self.globalEnv.WhereIs('$CXX')
        cxx_escaped = cxx.replace('/', '_').replace('\\', '_').replace(':', '_')
        now_file = os.path.join('build', 'compiler_timestamps', cxx_escaped + '.last_update')
        then_file = os.path.join('build', 'compiler_timestamps', cxx_escaped + '.mtime')
        self.compiler_timestamp_file = now_file

        # Run it now if needed so that we don't need to reconfigure twice since the configure job
        # depends on the timestamp.
        touch_compiler_timestamps.run_if_needed(cxx, then_file, now_file)


        self.builds.append(dict(
            rule='COMPILER_TIMESTAMPS',
            inputs=cxx,
            outputs=[then_file, now_file]))

        for build in self.builds:
            if build['rule'] in ('CC', 'CXX', 'SHCC', 'SHCXX'):
                build.setdefault('implicit', []).append(self.compiler_timestamp_file)

    def add_error_code_check(self):
        timestamp_file = os.path.join('build', 'compiler_timestamps', 'error_code_check.timestamp')
        command = self.make_command(
                '$PYTHON buildscripts/errorcodes.py -q --list-files \n ( echo "" > {} )'.format(
                    timestamp_file))
        self.builds.append(dict(
            rule='EXEC',
            implicit=self.ninja_file,
            outputs=timestamp_file,
            variables=dict(
                command=command,
                description='Checking error codes and waiting for next compile to finish',
                deps='msvc',
                msvc_deps_prefix='scanning file: ',
                )))

        # Make this an order_only input to linking stages. This ensures that it happens on every
        # build but is still allowed to happen in parallel with compilation. This should get it out
        # of the critical path so that it doesn't actually affect build times, with the downside
        # that it detects errors later.
        for build in self.builds:
            if build['rule'] in ('LINK', 'SHLINK', 'AR'):
                build.setdefault('order_only', []).append(timestamp_file)

    def add_icecream_check(self):
        # Run the verification script on every build to warn the user if icecream is not running
        dummy_target = '_verify_icecream_setup'
        self.builds.append(dict(
            rule='EXEC',
            inputs='_ALWAYS_BUILD',
            outputs=dummy_target,
            variables=dict(
                command='$PYTHON ' + verify_icecream_script,
                description='Checking for a proper icecream setup',
                )))

        # Do this before trying to compile since it is very quick and we want to alert if we are in
        # a bad state.
        for build in self.builds:
            if build['rule'] in ('CC', 'CXX', 'SHCC', 'SHCXX'):
                build.setdefault('order_only', []).append(dummy_target)


    def set_up_ccache(self):
        for rule in ('CC', 'CXX', 'SHCC', 'SHCXX'):
            if rule in self.tool_commands:
                self.tool_commands[rule] = '{} {}'.format(
                        self.globalEnv['_NINJA_CCACHE'],
                        self.tool_commands[rule])

    def set_up_icecc(self):
        cc = self.globalEnv.WhereIs('$CC')
        cxx = self.globalEnv.WhereIs('$CXX')

        # This is a symlink that points to the real environment file with the md5sum name. This is
        # important because icecream assumes that same-named environments are identical, but we need
        # to give ninja a fixed name for dependency tracking.
        version_file = 'build/icecc_envs/{}.tar.gz'.format(cc.replace('/', '_'))
        env_flags = [
            'CCACHE_PREFIX=' + self.globalEnv['_NINJA_ICECC'],
        ]
        compile_flags = []

        if self.globalEnv.ToolchainIs('clang'):
            env_flags += [ 'ICECC_CLANG_REMOTE_CPP=1' ]
            if self.globalEnv['_NINJA_CCACHE_VERSION'] >= [3, 4, 1]:
                # This needs the fix for https://github.com/ccache/ccache/issues/185 to work.
                env_flags += [ 'CCACHE_NOCPP2=1' ]
                compile_flags += [ '-frewrite-includes' ]

            if self.globalEnv.TargetOSIs("darwin"):
                version_file = fetch_icecream_tarball()
                assert os.path.exists(version_file)
                env_flags += [ 'ICECC_VERSION=x86_64:%s' % version_file ]
            else:
                env_flags += [ 'ICECC_VERSION=$$(realpath "%s")' % version_file ]
                self.builds.append(dict(
                    rule='MAKE_ICECC_ENV',
                    inputs=icecc_create_env,
                    outputs=version_file,
                    implicit=[cc, self.compiler_timestamp_file],
                    variables=dict(
                        cmd='{icecc_create_env} --clang {clang} {compiler_wrapper} {out}'.format(
                            icecc_create_env=icecc_create_env,
                            clang=os.path.realpath(cc),
                            compiler_wrapper='/bin/true', # we require a new enough iceccd.
                            out=version_file),
                        )
                    ))
        else:
            env_flags += [ 'ICECC_VERSION=$$(realpath "%s")' % version_file ]
            env_flags += [ 'CCACHE_NOCPP2=1' ]
            compile_flags += [ '-fdirectives-only' ]

            self.builds.append(dict(
                rule='MAKE_ICECC_ENV',
                inputs=icecc_create_env,
                outputs=version_file,
                implicit=[cc, cxx, self.compiler_timestamp_file],
                variables=dict(
                    cmd='{icecc_create_env} --gcc {gcc} {gxx} {out}'.format(
                        icecc_create_env=icecc_create_env,
                        gcc=os.path.realpath(cc),
                        gxx=os.path.realpath(cxx),
                        out=version_file),
                    )
                ))

        for rule in ('CC', 'CXX', 'SHCC', 'SHCXX'):
            if rule in self.tool_commands:
                self.tool_commands[rule] = (
                        ' '.join(env_flags + [self.tool_commands[rule]] + compile_flags))

        for build in self.builds:
            if build['rule'] in ('CC', 'CXX', 'SHCC', 'SHCXX'):
                build.setdefault('order_only', []).append(version_file)

        # Run links through icerun to inform the scheduler that we are busy and to prevent running
        # hundreds of parallel links.
        for rule in ('LINK', 'SHLINK'):
            if rule in self.tool_commands:
                self.tool_commands[rule] = '{} {}'.format(
                        self.globalEnv['_NINJA_ICERUN'],
                        self.tool_commands[rule])

    def find_aliases(self):
        for alias in SCons.Node.Alias.default_ans.values():
            if str(alias) in self.built_targets:
                # For some reason we sometimes define a task then alias it to itself.
                continue

            if alias.get_builder() == SCons.Environment.AliasBuilder:
                # "pure" aliases
                self.aliases[str(alias)] = [str(s) for s in alias.sources]
                pass
            else:
                # Ignore these for now
                assert (str(alias) in ('dist', 'lint'))

        # Fix integration_tests alias to point to files rather than directories.
        # TODO remove after CR merged
        integration_tests_dir = os.path.join('build', 'integration_tests')
        if integration_tests_dir in self.aliases['integration_tests']:
            self.aliases['integration_tests']= [t for t in self.built_targets
                                                  if t.startswith(integration_tests_dir)]

    def hide_slow_compile_latency(self):
        # Some of our TUs take substantially longer to compile. Try to start them first to mask
        # their high latency by compiling everything else while they are going. The list of TUs was
        # determined empirically by timing each compile at -j1 (NINJA_STATUS='%e %p ' makes this
        # easier). We should probably revisit this list periodically.

        slow_tu_parts= [
            "topology_coordinator_v1_test",
            "storage_interface_impl_test",
            "expression_convert_test",
            "transaction_coordinator_futures_util_test",
            "options_parser_test",
            "future_test_future", # multiple slow TUs
            "query_planner_test",
            "replication_coordinator_impl_test",
            "expression_test",
            "transport_layer_asio", # not as quite slow as others, but orig order put it very late.
        ]

        # This is a total hack. Ninja's "scheduler" that decides which task to run next relies on
        # the order of a std::set<Edge*>. By ordering tasks higher, they seem to get lower pointer
        # values, and therefore run earlier. Hopefully we can replace this with a proper priority
        # system if ninja ever implements one.
        def priority(build):
            if build['rule'].endswith('CXX') and any(s in build['outputs'] for s in slow_tu_parts):
                # Slowest tasks go first.
                return 0
            if build['rule'] == 'CXX':
                # On average, CXX tasks take ~50% longer than SHCXX tasks, so they should be started
                # earlier. OTOH, they are "leaf" jobs. On balance, doing them earlier seems to make
                # builds go faster.
                return 10
            if build['rule'] in ('SHLINK', 'AR'):
                # Link intermediate libs when ready rather than waiting until everything is ready.
                return 20
            if build['rule'] == 'LINK':
                # Ditto final links.
                return 30
            if build['rule'] in ('SHCXX', 'SHCC', 'CC'):
                # Do third_party first, both because the mozjs "unified" TUs are fairly slow, and to
                # unblock links.
                if 'third_party' in build['outputs']:
                    return 40
                # All of our library code.
                # TODO it may be worth ordering by LIBDEPS depth, deepest first.
                return 50

            # Everything else gets ordered early so they don't unnecessarily delay tasks that depend
            # on them.
            return -99

        self.builds.sort(key=priority)

    def find_build_nodes(self):
        seen = set()
        # Convert this to a list because SCons is still changing this
        # dict when we start iterating which causes python to raise an
        # exception
        for n in list(self.globalEnv.fs.Top.root._lookupDict.values()):
            if not SCons.Node.is_derived_node(n): continue
            if isinstance(n, SCons.Node.FS.Dir): continue
            if str(n.executor).startswith('write_uuid_to_file('): continue
            if os.path.join('','sconf_temp','conftest') in str(n): continue
            if str(n).startswith(os.path.join('build','install','')): continue

            # We see each build task once per target, but we handle all targets the first time.
            if id(n.executor) not in seen:
                seen.add(id(n.executor))
                try:
                    self.handle_build_node(n)
                except:
                    print()
                    print("Failed on node:", n)
                    print("Command:", n.executor)
                    print()
                    raise

        self.add_unit_test_shortcuts()

        for build in self.builds:
            # Make everything build by scons depend on the ninja file. This makes them transitively
            # depend on all of the scons dependencies so scons gets a chance to rebuild them
            # whenever any scons files change.
            if build['rule'] == 'SCONS':
                build.setdefault('implicit', []).append(self.ninja_file)

    def make_command(self, cmd):
        lines = cmd.split('\n')
        if len(lines) == 1:
            return cmd # no changes needed

        cmd = ' && '.join(lines)
        if self.globalEnv.TargetOSIs('windows'):
            cmd = 'cmd /c ' + cmd
        return cmd

    def add_unit_test_shortcuts(self):
        for key in self.unittest_shortcuts:
            self.builds.append(self.unittest_shortcuts[key])

    def handle_build_node(self, n):
        # TODO break this function up

        if n.executor.post_actions:
            # We currently only use this to set the executable bits on files, but we do it in
            # different ways in different places. For now, only support this usage.
            assert len(n.executor.post_actions) == 1
            assert len(n.executor.action_list) == 1
            assert n.executor.action_list[0] == SCons.Tool.textfile._subst_builder.action
            if str(n.executor.post_actions[0]) != 'chmod 755 $TARGET':
                assert str(n.executor.post_actions[0]).startswith('Chmod(')
                assert 'oug+x' in str(n.executor.post_actions[0])
            n.executor.post_actions = []
            do_chmod = True
        else:
            do_chmod = False

        assert n.has_builder()
        assert not n.side_effect
        assert not n.side_effects
        assert n.builder.action
        assert n.executor
        assert not n.executor.post_actions
        assert not n.executor.overridelist
        assert len(n.executor.get_action_list()) == 1

        action = n.executor.get_action_list()[0]
        myEnv = n.executor.get_build_env()
        targets = n.executor.get_all_targets()
        sources = n.executor.get_all_sources()
        implicit_deps = strmap(n.depends)

        for target in targets:
            if target.always_build:
                implicit_deps.append('_ALWAYS_BUILD')

            target = str(target)
            # This is handled explicitly in write_regenerator.
            if target.endswith('.ninja'): return

            self.built_targets.add(target)
            if target.endswith('.h') or target.endswith('.hpp'):
                self.generated_headers.add(target)

        if action == SCons.Tool.install.install_action:
            assert len(targets) == 1
            assert len(sources) == 1
            self.builds.append(dict(
                rule='INSTALL',
                outputs=strmap(targets),
                inputs=strmap(sources),
                implicit=implicit_deps
                ))
            return

        if action == SCons.Tool.textfile._subst_builder.action:
            implicit_deps.append(subst_file_script)
            args = dict(do_chmod=do_chmod, subs=myEnv['SUBST_DICT'])
            self.builds.append(dict(
                rule='SCRIPT_RSP',
                outputs=strmap(targets),
                inputs=strmap(sources),
                implicit=implicit_deps,
                variables={
                    'rspfile_content': ninja_syntax.escape(json.dumps(args)),
                    'script': subst_file_script,
                    }
                ))
            return

        if len(targets) == 1 and any(str(targets[0]).endswith(suffix)
                                     for suffix in ['tests.txt', 'benchmarks.txt']):
            if len(sources) == 1:
                assert isinstance(sources[0], SCons.Node.Python.Value)
                tests = sources[0].value
            else:
                # Unpatched builds put the list in sources.
                #TODO remove after CR merged
                tests = strmap(sources)

            implicit_deps.extend([test_list_script, self.ninja_file])
            self.builds.append(dict(
                rule='SCRIPT_RSP',
                outputs=strmap(targets),
                inputs=[],
                implicit=implicit_deps,
                variables={
                    'rspfile_content': ninja_syntax.escape(json.dumps(tests)),
                    'script': test_list_script,
                    }
                ))
            return

        if str(targets[0]) == 'compile_commands.json' and self.globalEnv['NINJA']:
            assert len(targets) == 1
            # Use ninja to generate the compile db rather than scons.
            self.builds.append(dict(
                rule='COMPILE_DB',
                outputs=strmap(targets),
                inputs=self.ninja_file,
                order_only=['generated-sources'], # These should be updated along with the compdb.
                ))
            return

        if isinstance(action, SCons.Action.ListAction):
            lines = str(n.executor).split('\n')
            if len(action.list) == 2:
                if lines[1] == 'noop_action(target, source, env)':
                    # Remove the noop_action we attach to thin archive builds.
                    n.executor.set_action_list(action.list[0])
                    assert len(n.executor.get_action_list()) == 1
                    action = n.executor.get_action_list()[0]

                elif lines[1] == 'embedManifestExeCheck(target, source, env)':
                    # We don't use this.
                    assert not myEnv.get('WINDOWS_EMBED_MANIFEST')
                    n.executor.set_action_list(action.list[0])
                    assert len(n.executor.get_action_list()) == 1
                    action = n.executor.get_action_list()[0]

            # Strip out the functions from shared library builds.
            if '$SHLINK' in str(n.executor):
                # Linux or Windows
                assert len(lines) == 3 or len(lines) == 5

                # Run the check now. It doesn't need to happen at runtime.
                assert lines[0] == 'SharedFlagChecker(target, source, env)'
                SCons.Defaults.SharedFlagChecker(targets, sources, myEnv)

                # We don't need this right now, so just assert that we don't. It can be added if we
                # ever need it.
                assert lines[2] == 'LibSymlinksActionFunction(target, source, env)' or \
                    lines[4] == 'LibSymlinksActionFunction(target, source, env)'
                for target in targets:
                    assert not getattr(getattr(targets[0],'attributes', None), 'shliblinks', None)

                # TODO: Windows - remove .def from from sources
                # and extend _LIBFLAGS with /def:

                # Now just make it the "real" action.
                n.executor.set_action_list(action.list[1])
                assert len(n.executor.get_action_list()) == 1
                action = n.executor.get_action_list()[0]

        if str(n.executor) == 'jsToH(target, source, env)':
            # Patch over the function to do it outside of scons.
            #TODO remove after CR merged
            cmd = '$PYTHON site_scons/site_tools/jstoh.py $TARGET $SOURCES'
            implicit_deps.append('site_scons/site_tools/jstoh.py')
            n.executor.set_action_list([Action(cmd)])
            assert len(n.executor.get_action_list()) == 1
            action = n.executor.get_action_list()[0]

        if any(str(n.executor).startswith('${TEMPFILE('+quote) for quote in ('"', "'")):
            # Capture the real action under the tempfile.
            cmd = []
            def TEMPFILE(cmd_, comstr=None):
                cmd.append(cmd_)
            myEnv['TEMPFILE'] = TEMPFILE
            myEnv.subst(str(n.executor), executor=n.executor)
            cmd = cmd[0]
            assert '(' not in cmd
            n.executor.set_action_list([Action(cmd)])
            assert len(n.executor.get_action_list()) == 1
            action = n.executor.get_action_list()[0]

        if str(n.executor).endswith('${TEMPFILE(SOURCES[1:])}'):
            assert len(targets) == 1
            prefix = str(n.executor)[:-len('${TEMPFILE(SOURCES[1:])}')]
            cmd = myEnv.subst(prefix, executor=n.executor) + (' @%s.rsp'%targets[0])
            self.builds.append(dict(
                rule='EXEC_RSP',
                outputs=strmap(targets),
                inputs=strmap(sources[1:]),
                implicit=implicit_deps + [str(sources[0])],
                variables = {'command': cmd},
                ))
            return

        # TODO find a better way to find things that are functions
        needs_scons = (isinstance(n.executor.get_action_list()[0], SCons.Action.FunctionAction)
                       or '(' in str(n.executor))
        if needs_scons:
            sources = [s for s in sources if not isinstance(s, SCons.Node.Python.Value)]
            self.builds.append(dict(
                rule='SCONS',
                outputs=strmap(targets),
                inputs=strmap(sources),
                implicit=implicit_deps
                ))
            return

        tool = str(n.executor).split(None, 1)[0]
        if tool == '$IDLC' and myEnv.get('IDL_HAS_INLINE_DEPENDENCIES'):
            if n.implicit:
                implicit_deps += strmap(n.implicit)

            # Important:
            # Originally we ran the IDL scanner during build.ninja file generation but this is
            # single-thread and slow as the number of IDL files has grown to greater then 100.
            # Now, we let IDL tell Ninja its dependencies. Unfortunately, IDL does not have a good
            # way to this with its implicit dependency caching and multiple outputs due to a
            # limitation in ninja.
            # See https://github.com/ninja-build/ninja/pull/1534
            #
            # Instead we split IDL file generation into "two" phases:
            # 1. Generate the header and the cpp file as normal but only tell Ninja about the header
            # 2. "Generate" the cpp file by telling Ninja it depends on the header file via a
            #    phony rule
            #
            idl_cpp_file = strmap(targets)[0]
            idl_header_file = strmap(targets)[1]

            # Append --write-dependencies-inline so that IDL generates a list of dependencies when
            # it runs
            idl_command = self.make_command(myEnv.subst(str(n.executor), executor=n.executor) \
                + " --write-dependencies-inline")

            implicit_deps.extend(self.idl_deps)

            # Lie to Ninja by saying it only generates a header
            self.builds.append(dict(
                rule='EXEC',
                outputs=[idl_header_file],
                inputs=strmap(sources),
                implicit=implicit_deps,
                variables={
                    'command' : idl_command,
                    'deps' : 'msvc',
                    'msvc_deps_prefix' : 'import file:',
                    }
                ))

            # Tell Ninja the cpp file is "generated" from the header file
            self.builds.append(dict(
                rule='phony',
                outputs=[ idl_cpp_file ],
                inputs=[ idl_header_file ],
                ))
            return
        elif tool not in ('$CC', '$CXX', '$SHCC', '$SHCXX', '$LINK', '$SHLINK', '$AR', '$RC'):
            list_targets = strmap(targets)
            list_sources = strmap(sources)

            n.scan() # We need this for IDL.
            implicit_deps += strmap(n.implicit)
            self.builds.append(dict(
                rule='EXEC',
                outputs=list_targets,
                inputs=list_sources,
                implicit=implicit_deps,
                variables={
                    'command': self.make_command(myEnv.subst(str(n.executor), executor=n.executor)),
                    }
                ))

            # Starting in SERVER-43047, there is no more install rules for unittests but there is a
            # rule for running them with @. Use that rule as a guideline to build our own "+" rule.
            # The plus rule is surperior since it uses the console logger to avoid interlacing of
            # tests.
            if list_targets[0].startswith("@"):
                test_name = list_targets[0]
                test_name = '+' + test_name[1:]

                # These take priority over unit test shortcut names.
                self.unittest_skipped_shortcuts.add(test_name)
                if test_name in self.unittest_shortcuts.keys():
                    del self.unittest_shortcuts[test_name]

                self.builds.append(dict(
                        rule='RUN_TEST',
                        outputs=test_name,
                        inputs=list_sources
                    ))

                # Add shortcuts for this unit test.
                for unit_test_source_file in \
                        n.executor.get_all_children()[0].executor.get_all_sources():
                    # Get the file name
                    pos_last_slash = str(unit_test_source_file).rfind(os.path.sep)
                    # Strip the '.o' or '.obj'
                    stripped_name = str(unit_test_source_file)[pos_last_slash + 1:]
                    pos_last_dot = stripped_name.find(".")
                    stripped_name = stripped_name[:pos_last_dot]

                    if "_test" in stripped_name[len(stripped_name) - 5:]:
                        # Add suffix to tests on Windows to match other unit tests
                        suffix = ".exe" if self.globalEnv.TargetOSIs('windows') else ""
                        stripped_name = '+' + stripped_name + suffix
                        if (stripped_name not in self.unittest_shortcuts and stripped_name not
                                in self.unittest_skipped_shortcuts):
                            # Add a shortcut for the given unit test file name.
                            self.unittest_shortcuts[stripped_name] = dict(
                                rule='RUN_TEST',
                                outputs=stripped_name,
                                inputs=list_sources
                            )
                        elif stripped_name in self.unittest_shortcuts:
                            # There are multiple unit tests with the same file name. So we cannot
                            # create a shortcut for this test name.
                            del self.unittest_shortcuts[stripped_name]
                            self.unittest_skipped_shortcuts.add(stripped_name)

            return

        self.tool_paths.add(myEnv.WhereIs(tool))

        tool = tool.strip('${}')
        # This is only designed for tools that use $TARGET and $SOURCES not $TARGETS or $SOURCE.
        cmd = self.make_command(str(n.executor).replace('$TARGET.windows', '$out')
                                               .replace('$TARGET','$out')
                                               .replace('$CHANGED_SOURCES', '$in')
                                               .replace('$SOURCES.windows', '$in')
                                               .replace('$_SHLINK_TARGETS', '$out')
                                               .replace('$_SHLINK_SOURCES', '$in')
                                               .replace('$SOURCES','$in'))
        assert 'TARGET' not in cmd
        assert 'SOURCE' not in cmd
        if tool in self.tool_commands:
            assert cmd == self.tool_commands[tool]
        else:
            self.tool_commands[tool] = cmd

        is_link_model_object = myEnv['_LIBDEPS'] == '$_LIBDEPS_OBJS'
        libdeps = []
        if tool in ('LINK', 'SHLINK'):
            if is_link_model_object:
                libdep_func = myEnv[myEnv['_LIBDEPS'].strip('${}')]
            else:
                libdep_func = myEnv['_LIBDEPS_GET_LIBS']
            assert callable(libdep_func)
            libdeps = strmap(libdep_func(sources, targets, myEnv, False))
            if myEnv.ToolchainIs('msvc'):
                implicit_deps += [split_lines_script]

        if tool == 'RC':
            # We need to use the scons scanner for windows rc files since the rc tool doesn't have
            # anything like /showIncludes.
            n.scan()
            implicit_deps += strmap(n.implicit)
            self.rc_files.append(str(sources[0])) # Regenerate build.ninja when this file changes.

        myVars = {}

        for word in shlex.split(cmd, posix=myEnv.TargetOSIs('posix')):
            if not word.startswith('$'): continue
            if word in ('$in', '$out'): continue

            name = word.strip('${}')
            #TODO find some way to make sure they aren't used elsewhere in the rule
            assert not name.startswith('TARGET')
            assert not name.startswith('SOURCE')
            assert re.match(r'^[a-zA-Z_]*$', name)

            if not (name == '_LIBFLAGS' and is_link_model_object):
                mySubst = myEnv.subst(word, executor=n.executor)
            else:
                # Expanding $_LIBDEPS in scons is very slow. Do it ourselves.
                (pre, post) = myEnv['_LIBFLAGS'].split(' $_LIBDEPS ')
                mySubst = ' '.join([myEnv.subst(pre, executor=n.executor)] +
                                    ['"%s"'%libdep for libdep in libdeps] +
                                    [myEnv.subst(post, executor=n.executor)])

            if name in ('_LIBFLAGS', '_PDB', '_MSVC_OUTPUT_FLAG'):
                # These are never worth commoning since they are always different.
                myVars[name] = mySubst
                continue

            if name not in self.vars:
                self.vars[name] = self.globalEnv.subst(word)
            if mySubst != self.vars[name]:
                if mySubst.startswith(self.vars[name]):
                    mySubst = '${%s}%s'%(name, mySubst[len(self.vars[name]):])
                over = self.overrides.setdefault(name, {})
                num = over.setdefault(mySubst, len(over))
                myVars[name] = '$%s_%s'%(name, num)

        # Since the scons command line uses '$TARGET' it only expects the first target to be passed.
        # Everything else must be an implicit output. Additionally, removing .dwo files from targets
        # to work around ninja limitation that build rules using the 'depslog' can't have multiple
        # outputs. ccache will still handle them correctly, the only real downside is that the
        # 'clean' tool won't remove them. An alternative solution would be removing the 'deps=gcc'
        # setting from the rules definition, but that has significant overhead (~1s no-op builds) so
        # I don't think the tradeoff is worth it.
        # For more details see: https://github.com/ninja-build/ninja/issues/1184
        targets = [t for t in strmap(targets) if not t.endswith('.dwo')]
        toolPath = myEnv.WhereIs('$'+tool)
        assert toolPath, 'Unable to find the location of tool "%s"' % tool

        list_sources = strmap(sources)

        if tool == 'SHLINK' and myEnv.ToolchainIs('msvc'):
            # A Def file is considered input but they must be transformed to an argument to link.exe
            # Make the def file an implicit dependency instead an input after removing it from
            # sources
            defs = [def_file for def_file in list_sources if ".def" in def_file]
            assert len(defs) <= 1
            if len(defs) == 1:
                def_file = defs[0]
                list_sources.remove(def_file)
                implicit_deps.append(def_file)
                myVars['_LIBFLAGS'] += " /def:" + def_file

        self.builds.append(dict(
            rule=tool,
            outputs=targets[0],
            implicit_outputs=targets[1:],
            inputs=list_sources,
            implicit=implicit_deps + libdeps + [toolPath],
            order_only=['_generated_headers']
                       if tool in ('CC', 'CXX', 'SHCC', 'SHCXX', 'RC')
                       else [],
            variables=myVars,
            ))


    def write(self):
        # Defer touching the actual .ninja file until we are done building the contents to minimize
        # the window where the file isn't complete.
        content = io.StringIO() if sys.version_info >= (3,) else io.BytesIO()

        # make ninja file directly executable. (bit set later)
        # can't use ninja.comment() because it adds a space after the !
        if self.globalEnv['NINJA']:
            content.write('#!%s -f\n\n'%self.globalEnv['NINJA'])

        ninja = ninja_syntax.Writer(content, width=100)
        ninja.comment('Generated by scons. DO NOT EDIT.')

        self.write_vars(ninja)
        self.write_rules(ninja)
        self.write_builds(ninja)
        self.write_regenerator(ninja)

        ninja.newline()
        for default in sorted(strmap(DEFAULT_TARGETS)):
            ninja.default(default)

        # Tell vim and emacs not to break up long lines.
        ninja.newline()
        ninja.comment('vim: set textwidth=0 :')
        ninja.comment('-*- eval: (auto-fill-mode -1) -*-')
        with open(self.ninja_file, 'w') as f:
            f.write(content.getvalue())
        if self.globalEnv['NINJA'] and not self.globalEnv.TargetOSIs('windows'):
            os.chmod(self.ninja_file, 0o755)

    def write_vars(self, ninja):
        # We can probably drop this to 1.5, but I've only tested with 1.7.
        ninja.newline()
        ninja.variable('ninja_required_version', '1.7')
        if GetOption('ninja_builddir'):
            ninja.variable('builddir', GetOption('ninja_builddir'))

        ninja.newline()
        ninja.variable('scons_args',
                ['"%s"'%arg for arg in sys.argv[1:] if not arg in COMMAND_LINE_TARGETS])

        ninja.newline()
        for name in sorted(self.vars):
            ninja.variable(name, self.vars[name])

        ninja.newline()
        for name in sorted(self.overrides):
            for num, val in sorted((num, val) for (val, num) in self.overrides[name].items()):
                ninja.variable('%s_%s'%(name, num), val)

    def write_rules(self, ninja):
        ninja.newline()

        local_pool = None
        compile_pool = None
        if self.globalEnv.get('_NINJA_ICECC'):
            # The local_pool is used for all operations that don't go through icecc and aren't
            # already using another pool. This ensures that we don't overwhelm the system when
            # using very high -j values.
            local_pool = 'local'
            ninja.pool('local', multiprocessing.cpu_count())

            if self.globalEnv['_NINJA_ICECC'] == self.globalEnv['_NINJA_ICERUN']:
                # Limit concurrency so we don't start a bunch of tasks only to have them bottleneck
                # in icerun. This is especially helpful when there is an early compile failure so
                # that we don't keep starting compiles after the first failure.
                compile_pool = local_pool

            ninja.rule('MAKE_ICECC_ENV',
                command = '$cmd',
                pool = 'console', # slow, so show progress.
                description = 'MAKE_ICECC_ENV $out')

        ninja.rule('RUN_TEST',
                command='$in',
                description='RUN_TEST $in',
                pool='console') # show live output.

        ninja.rule('EXEC',
                command='$command',
                pool=local_pool)
        ninja.rule('EXEC_RSP',
                command='$command',
                pool=local_pool,
                rspfile = '$out.rsp',
                rspfile_content = '$in',
                )

        ninja.rule('INSTALL',
                command = '$COPY $in $out',
                pool=local_pool,
                description = 'INSTALL $out')

        ninja.rule('SCRIPT_RSP',
            command = '$PYTHON $script $in $out $out.rsp',
            pool=local_pool,
            rspfile = '$out.rsp',
            rspfile_content = '$rspfile_content',
            restat = 1,
            description = "GEN $out")

        ninja.rule("COMPILER_TIMESTAMPS",
            command="$PYTHON %s $in $out"%(touch_compiler_timestamps_script),
            pool=local_pool,
            restat=1,
            description="Checking for compiler upgrades")

        ninja.rule('SCONS',
            command = '$PYTHON %s -Q $scons_args $out'%(sys.argv[0]),
            pool = 'console',
            description = 'SCONSGEN $out',
            restat=1)

        if self.globalEnv['NINJA']:
            cmd = self.globalEnv['NINJA'] + ' -f $in -t compdb CXX CC SHCXX SHCC > $out.tmp'
            if self.globalEnv.TargetOSIs('windows'):
                cmd = 'cmd /c ' + cmd + ' && move /y $out.tmp $out'
            else:
                cmd = cmd + ' && mv -f $out.tmp $out'
            ninja.rule('COMPILE_DB',
                    command = cmd,
                    pool=local_pool,
                    description = 'COMPILE_DB $out')

        if self.globalEnv.ToolchainIs('gcc', 'clang'):
            # ninja ignores leading spaces so this will work fine if empty.
            if 'CXX' in self.tool_commands:
                ninja.rule('CXX',
                    deps = 'gcc',
                    depfile = '$out.d',
                    command = '%s -MMD -MF $out.d'%(self.tool_commands['CXX']),
                    pool=compile_pool,
                    description = 'CXX $out')
            if 'SHCXX' in self.tool_commands:
                ninja.rule('SHCXX',
                    deps = 'gcc',
                    depfile = '$out.d',
                    command = '%s -MMD -MF $out.d'%(self.tool_commands['SHCXX']),
                    pool=compile_pool,
                    description = 'SHCXX $out')
            if 'CC' in self.tool_commands:
                ninja.rule('CC',
                    deps = 'gcc',
                    depfile = '$out.d',
                    command = '%s -MMD -MF $out.d'%(self.tool_commands['CC']),
                    pool=compile_pool,
                    description = 'CC $out')
            if 'SHCC' in self.tool_commands:
                ninja.rule('SHCC',
                    deps = 'gcc',
                    depfile = '$out.d',
                    command = '%s -MMD -MF $out.d'%(self.tool_commands['SHCC']),
                    pool=compile_pool,
                    description = 'SHCC $out')
            if 'SHLINK' in self.tool_commands:
                command = self.tool_commands['SHLINK']
                i = command.find('$SHLINK ') + len('$SHLINK')
                prefix = command[:i]
                args = command[i + 1:]
                ninja.rule('SHLINK',
                    command = prefix + ' @$out.rsp',
                    rspfile = '$out.rsp',
                    rspfile_content = args,
                    pool=local_pool,
                    description = 'DYNLIB $out')
            if 'LINK' in self.tool_commands:
                command = self.tool_commands['LINK']
                i = command.find('$LINK ') + len('$LINK')
                prefix = command[:i]
                args = command[i + 1:]
                ninja.rule('LINK',
                    command = prefix + ' @$out.rsp',
                    rspfile = '$out.rsp',
                    rspfile_content = args,
                    pool=local_pool,
                    description = 'LINK $out')
            if 'AR' in self.tool_commands:
                # We need to remove $out because the file existing can confuse ar. This is particularly
                # a problem when switching between thin and non-thin archive files.
                ninja.rule('AR',
                    command = 'rm -f $out && ' + self.tool_commands['AR'],
                    pool=local_pool,
                    description = 'STATICLIB $out')
        else:
            if 'CXX' in self.tool_commands:
                ninja.rule('CXX',
                    deps = 'msvc',
                    command = '%s /showIncludes'%(self.tool_commands['CXX']),
                    description = 'CXX $out')
            if 'SHCXX' in self.tool_commands:
                ninja.rule('SHCXX',
                    deps = 'msvc',
                    command = '%s /showIncludes'%(self.tool_commands['SHCXX']),
                    description = 'SHCXX $out')
            if 'CC' in self.tool_commands:
                ninja.rule('CC',
                    deps = 'msvc',
                    command = '%s /showIncludes'%(self.tool_commands['CC']),
                    description = 'CC $out')
            if 'RC' in self.tool_commands:
                ninja.rule('RC',
                    command = self.tool_commands['RC'],
                    description = 'RC $out')
            if 'AR' in self.tool_commands:
                ninja.rule('AR',
                    command = self.tool_commands['AR'],
                    description = 'STATICLIB $out')
            if 'LINK' in self.tool_commands:
                ninja.pool('winlink', GetOption('link-pool-depth'))
                ninja.rule('LINK',
                    command = 'cmd /c $PYTHON %s $out.rsp && $LINK @$out.rsp'%split_lines_script,
                    rspfile = '$out.rsp',
                    rspfile_content = self.tool_commands['LINK'].replace('$LINK ', ''),
                    pool='winlink',
                    description = 'LINK $out')
            if 'SHLINK' in self.tool_commands:
                if 'LINK' not in self.tool_commands:
                    ninja.pool('winlink', GetOption('link-pool-depth'))
                # Workaround mslink.py's dll handling by transforming $out to switch to link.exe
                ninja.rule('SHLINK',
                    command = 'cmd /c $PYTHON %s $out.rsp && $SHLINK @$out.rsp'%split_lines_script,
                    rspfile = '$out.rsp',
                    rspfile_content = self.tool_commands['SHLINK'].replace('$SHLINK ', '').replace('$out', '/OUT:$out'),
                    pool='winlink',
                    description = 'SHLINK $out')


    def write_builds(self, ninja):
        ninja.newline()
        for build in self.builds:
            ninja.build(**build)

        ninja.newline()
        for alias in sorted(self.aliases):
            ninja.build(alias, 'phony', strmap(self.aliases[alias]))

        ninja.newline()
        ninja.build('_generated_headers', 'phony', sorted(self.generated_headers))
        ninja.build('_ALWAYS_BUILD', 'phony')

    def write_regenerator(self, ninja):
        deps = flatten([
            'SConstruct',
            rglob('SConscript', 'src'),
            rglob('*.py', os.path.expanduser('~/.scons/')),
            rglob('*.py', 'site_scons'),
            rglob('*.py', 'buildscripts'),
            rglob('*.py', 'src/third_party/scons-2.5.0'),
            rglob('*.py', 'src/mongo/db/modules'),
            [self.globalEnv.WhereIs(tool) for tool in self.tool_paths],
            self.compiler_timestamp_file,
            self.rc_files, # We rely on scons to tell us the deps of windows rc files.
            ])

        if not self.globalEnv.get('_NINJA_USE_ERRCODE'):
            # Depend on git as position as well. This ensures that error codes are always checked
            # after rebase. It is also used for filling in MONGO_VERSION and MONGO_GIT_HASH.
            deps += flatten(['.git/HEAD', glob.glob('.git/refs/heads/*')])

        deps = sorted(set(dep.replace(' ', '\\ ')
                          for dep in deps
                          if dep and os.path.isfile(dep)))

        depfile = self.ninja_file + '.deps'
        with open(depfile, 'w') as f:
            f.write(self.ninja_file + ': ')
            f.write(' '.join(deps))

        ninja.newline()
        ninja.rule('GENERATOR',
            command = "$PYTHON %s $scons_args $out"%(sys.argv[0]),
            pool = 'console',
            generator = 1,
            description = 'Regenerating $out',
            depfile = depfile,
            restat=1)
        ninja.build(self.ninja_file, 'GENERATOR')

def configure(conf, env):
    if not COMMAND_LINE_TARGETS:
        print("*** ERROR: To prevent PEBKACs, the ninja module requires that you pass a target to scons.")
        print("*** You probably forgot to include build.ninja on the command line")
        print("***")
        print("*** If you really want to build mongod using scons, do so explicitly or pass the")
        print("*** --modules= flag to disable the ninja module.")
        Exit(1)

    ninja_files = [str(t) for t in BUILD_TARGETS if str(t).endswith('.ninja')]
    if not ninja_files:
        return

    try:
        print("Checking for updates to ninja module...")
        subprocess.check_call(['git', '-C', my_dir, 'fetch'])
        output = subprocess.check_output(['git', '-C', my_dir, 'log', '--oneline', '@..@{upstream}'])
        if output:
            print("***")
            print("*** Your ninja module is out of date. New commits:")
            print("***")
            print(output.decode('utf8'))
    except Exception as e:
        # Errors checking for updates shouldn't prevent building.
        print("Ignoring error checking for updates: ", e)

    if 'ninja' not in env.subst("$VARIANT_DIR"):
        print("*** WARNING: you should use a dedicated VARIANT_DIR for ninja builds to prevent")
        print("*** conflicts with scons builds. You can suppress this by including 'ninja'")
        print("*** in your VARIANT_DIR string.")
        print('***')

    if GetOption('cache'):
        print("*** ERROR: Remove --cache flags to make ninja generation work.")
        print("*** ccache is used automatically if it is installed.")
        Exit(1)

    if env.get('ICECC'): # flexible to support both missing and set to ''
        print("*** ERROR: Remove ICECC=icecc flag or set to '' to make ninja generation work.")
        print("*** Use --icecream instead.")
        Exit(1)

    env['NINJA'] = where_is(env, 'ninja')
    if not env['NINJA']:
        env['NINJA'] = where_is(env, 'ninja-build') # Fedora...

    # This is checking if we have a version of errorcodes.py that supports the --list-files flag
    # needed to correctly handle the dependencies in ninja. This will be re-run when changing to
    # an older commit since the .ninja file depends on everything in buildscripts.
    if hasattr(errorcodes, 'list_files'):
        if env["MONGO_VERSION"] != "0.0.0" or env["MONGO_GIT_HASH"] != "unknown":
            print("*** WARNING: to get the most out of ninja, pass these flags to scons:")
            print('*** MONGO_VERSION="0.0.0" MONGO_GIT_HASH="unknown"')
            print('*** This will run the scons config less often and can make ccache more efficient')
            print('***')
        else:
            env["_NINJA_USE_ERRCODE"] = True

    action_str = "Generating $TARGET"
    if env.ToolchainIs('gcc', 'clang'):
        # ninja buffers stdout which causes gcc and clang not to emit color. Force it on and let
        # ninja filter out the colors if the real stdout is redirected.
        env.Append(CCFLAGS=["-fdiagnostics-color=always"])

        using_gsplitdwarf = any('-gsplit-dwarf' in env[var]
                                for var in ('CCFLAGS', 'CFLAGS', 'CXXFLAGS'))

        if using_gsplitdwarf and not env.TargetOSIs('linux'):
            print("*** ERROR: -gsplit-dwarf is only supported on Linux.")
            Exit(1)

        if GetOption('cache_disable') or GetOption('pch'):
            # Until this is merged, disable ccache + pch: https://github.com/ccache/ccache/pull/160
            env['_NINJA_CCACHE'] = ''
        else:
            env['_NINJA_CCACHE'] = where_is(env, 'ccache')
        if env['_NINJA_CCACHE']:
            action_str += " with ccache support (pass --no-cache to scons to disable)"
            if env.ToolchainIs('clang'):
                # Needed to make clang++ play nicely with ccache. Ideally this would use
                # AddToCCFLAGSIfSupported but that is available to modules.
                env.Append(CCFLAGS=["-Qunused-arguments"])

            settings = (subprocess.check_output([env['_NINJA_CCACHE'], '-p'])
                            .decode('utf8'))
            if 'max_size = 5.0G' in settings:
                print('*** ccache is using the default 5GB cache size. You can raise it by running:')
                print('*** ccache -o max_size=20G')
                print('***')

            if 'run_second_cpp = false' in settings:
                # This defaults to true in new versions. Our codebase generates spurious warnings
                # when it is false because the compiler can't see what is part of a macro expansion.
                print('*** ERROR: Change the ccache run_second_cpp flag to true by running:')
                print('*** ccache -o run_second_cpp=true')
                print('***')
                Exit(1)

            ccache_version_raw = (subprocess.check_output([env['_NINJA_CCACHE'], '--version'])
                                            .decode('utf8')
                                            .split('\n', 1)[0]
                                            .split()[-1]
                                            .split('+')[0])
            env['_NINJA_CCACHE_VERSION'] = [int(s) for s in ccache_version_raw.split('.')]

            if using_gsplitdwarf:
                if env['_NINJA_CCACHE_VERSION']  < [3, 2, 3]:
                    print("*** ERROR: -gsplit-dwarf requires ccache >= 3.2.3. You have: " + version)
                    Exit(1)

        if GetOption('icecream'):
            if GetOption('pch'):
                print('*** ERROR: icecream is not supported with pch')
                Exit(1)
            if not env.TargetOSIs('linux', 'darwin'):
                print('ERROR: icecream is currently only supported on linux and darwin')
                Exit(1)
            if not env['_NINJA_CCACHE']:
                print('*** ERROR: icecream currently requires ccache')
                Exit(1)

            env['_NINJA_ICECC'] = where_is(env, 'icecc')
            if not env['_NINJA_ICECC']:
                print("*** ERROR: Can't find icecc.")
                Exit(1)

            env['_NINJA_ICERUN'] = where_is(env, 'icerun')
            if not env['_NINJA_ICERUN']:
                print("*** ERROR: Can't find icerun.")
                Exit(1)

            version = (subprocess.check_output([env['_NINJA_ICECC'], '--version'])
                        .decode('utf8')
                        .split()[1])
            if version < '1.1rc2' and version != '1.1' and version < '1.2':
                print("*** ERROR: This requires icecc >= 1.1rc2, but you have " + version)
                Exit(1)

            if any(flag.startswith('-fsanitize-blacklist') for flag in env['CCFLAGS']):
                print("*** WARNING: The -fsanitize-blacklist flag only works on local builds.")
                print('*** Automatically limiting build concurrency and disabling remote execution.')
                print('***')

                # Use icerun so the scheduler knows we are busy. Also helps when multiple developers
                # are using the same machine.
                env['_NINJA_ICECC'] = env['_NINJA_ICERUN']

    for ninja_file in ninja_files:
        cmd = env.Command(ninja_file, [], Action(makeNinjaFile, action_str))
        env.Precious(cmd) # Don't delete the .ninja file before building.
        env.NoCache(cmd)
        env.AlwaysBuild(cmd)
