import SCons
from SCons.Script import *

import os
import re
import sys
import glob
import json
import shlex
import fnmatch
import subprocess

my_dir = os.path.dirname(__file__)

try:
    import ninja_syntax
except ImportError:
    # Sometimes we can't import a sibling file. This makes it possible.
    sys.path.append(my_dir)
    import ninja_syntax

AddOption('--link-pool-depth',
        default=4,
        type='int',
        action='store',
        dest='link-pool-depth',
        help='WINDOWS ONLY: limit of concurrent links (default 4)')

split_lines_script = os.path.join(my_dir, 'split_lines.py')
subst_file_script = os.path.join(my_dir, 'subst_file.py')
test_list_script = os.path.join(my_dir, 'test_list.py')

def makeNinjaFile(target, source, env):
    assert not source
    ninja_file = NinjaFile(str(target[0]), env)
    ninja_file.write()

def rglob(pattern, root='.') :
    return [os.path.join(path, f)
            for path, dirs, files in os.walk(root)
            for f in fnmatch.filter(files, pattern)]

def where_is(env, exe):
    path = env.WhereIs(exe)
    if not path:
        # check a few places that are often on $PATH but scons hides.
        if os.path.exists('/usr/local/bin/'+exe):
            path = '/usr/local/bin/ccache'
        elif os.path.exists('/opt/local/bin/'+exe):
            path = '/opt/local/bin/'+exe

    # Normalize missing to '' rather than None
    return path if path else ''

def strmap(list):
    for node in list:
        assert isinstance(node, (str, SCons.Node.FS.Base, SCons.Node.Alias.Alias))
    return map(str, list)

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

        self.find_build_nodes()
        self.find_aliases()

        assert 'COPY' not in self.vars
        if self.globalEnv.TargetOSIs('windows'):
            self.vars['COPY'] = 'cmd /c copy'
        else:
            self.vars['COPY'] = 'install' # install seems faster than cp.

        assert 'PYTHON' not in self.vars
        self.vars['PYTHON'] = self.globalEnv.WhereIs('$PYTHON')

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

    def find_build_nodes(self):
        seen = set()
        for n in self.globalEnv.fs.Top.root._lookupDict.values():
            if not SCons.Node.is_derived_node(n): continue
            if isinstance(n, SCons.Node.FS.Dir): continue
            if str(n.executor).startswith('write_uuid_to_file('): continue
            if os.path.join('','sconf_temp','conftest') in str(n): continue
            if str(n).startswith(os.path.join('build','install','')): continue

            # We see each build task once per target, but we handle all targets the first time.
            if id(n.executor) not in seen:
                seen.add(id(n.executor))
                self.handle_build_node(n)

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

    def handle_build_node(self, n):
        # TODO break this function up
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
            self.builds.append(dict(
                rule='SCRIPT_RSP',
                outputs=strmap(targets),
                inputs=strmap(sources),
                implicit=implicit_deps,
                variables={
                    'rspfile_content': ninja_syntax.escape(json.dumps(myEnv['SUBST_DICT'])),
                    'script': subst_file_script,
                    }
                ))
            return

        if len(targets) == 1 and str(targets[0]).endswith('tests.txt'):
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
            # TODO consider removing this if the scons compilation_db tool becomes more reliable.
            cmd = '%s -f %s -t compdb CXX CC SHCXX SHCC > %s'%(
                    self.globalEnv['NINJA'],
                    self.ninja_file,
                    str(targets[0]))
            if self.globalEnv.TargetOSIs('windows'):
                cmd = 'cmd /c ' + cmd
            self.builds.append(dict(
                rule='EXEC',
                outputs=strmap(targets),
                implicit=self.ninja_file,
                order_only='generated-sources', # These should be updated along with the compdb.
                variables={
                    'command': cmd
                    },
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
                assert len(lines) == 3

                # Run the check now. It doesn't need to happen at runtime.
                assert lines[0] == 'SharedFlagChecker(target, source, env)'
                SCons.Defaults.SharedFlagChecker(targets, sources, myEnv)

                # We don't need this right now, so just assert that we don't. It can be added if we
                # ever need it.
                assert lines[2] == 'LibSymlinksActionFunction(target, source, env)'
                for target in targets:
                    assert not getattr(getattr(targets[0],'attributes', None), 'shliblinks', None)

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
            sources = filter(lambda s: not isinstance(s, SCons.Node.Python.Value), sources)
            self.builds.append(dict(
                rule='SCONS',
                outputs=strmap(targets),
                inputs=strmap(sources),
                implicit=implicit_deps
                ))
            return

        tool = str(n.executor).split(None, 1)[0]
        if tool not in ('$CC', '$CXX', '$SHCC', '$SHCXX', '$LINK', '$SHLINK', '$AR', '$RC'):
            self.builds.append(dict(
                rule='EXEC',
                outputs=strmap(targets),
                inputs=strmap(sources),
                implicit=implicit_deps,
                variables={
                    'command': self.make_command(myEnv.subst(str(n.executor), executor=n.executor)),
                    }
                ))
            return

        self.tool_paths.add(myEnv.WhereIs(tool))

        tool = tool.strip('${}')
        # This is only designed for tools that use $TARGET and $SOURCES not $TARGETS or $SOURCE.
        cmd = self.make_command(str(n.executor).replace('$TARGET.windows', '$out')
                                               .replace('$TARGET','$out')
                                               .replace('$CHANGED_SOURCES', '$in')
                                               .replace('$SOURCES.windows', '$in')
                                               .replace('$SOURCES','$in'))
        assert 'TARGET' not in cmd
        assert 'SOURCE' not in cmd
        if tool in self.tool_commands:
            assert cmd == self.tool_commands[tool]
        else:
            self.tool_commands[tool] = cmd

        libdeps = []
        if tool in ('LINK', 'SHLINK'):
            libdep_func = myEnv[myEnv['_LIBDEPS'].strip('${}')]
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

            mySubst = myEnv.subst(word, executor=n.executor)

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
        # Everything else must be an implicit output
        self.builds.append(dict(
            rule=tool,
            outputs=str(targets[0]),
            implicit_outputs=strmap(targets[1:]),
            inputs=strmap(sources),
            implicit=implicit_deps + libdeps + [myEnv.WhereIs('$'+tool)],
            order_only='_generated_headers'
                       if tool in ('CC', 'CXX', 'SHCC', 'SHCXX', 'RC')
                       else None,
            variables=myVars,
            ))


    def write(self):
        file = open(self.ninja_file, 'w')

        # make ninja file directly executable. (bit set later)
        # can't use ninja.comment() because it adds a space after the !
        if self.globalEnv['NINJA']:
            file.write('#!%s -f\n\n'%self.globalEnv['NINJA'])

        ninja = ninja_syntax.Writer(file, width=100)
        ninja.comment('Generated by scons. DO NOT EDIT.')

        self.write_vars(ninja)
        self.write_rules(ninja)
        self.write_builds(ninja)
        self.write_regenerator(ninja)

        ninja.newline()
        for default in sorted(strmap(DEFAULT_TARGETS)):
            ninja.default(default)

        ninja.close()
        if self.globalEnv['NINJA'] and not self.globalEnv.TargetOSIs('windows'):
            os.chmod(self.ninja_file, 0755)

    def write_vars(self, ninja):
        # We can probably drop this to 1.5, but I've only tested with 1.7.
        ninja.newline()
        ninja.variable('ninja_required_version', '1.7')

        ninja.newline()
        ninja.variable('scons_args',
                ['"%s"'%arg for arg in sys.argv[1:] if not arg in COMMAND_LINE_TARGETS])

        ninja.newline()
        for name in sorted(self.vars):
            ninja.variable(name, self.vars[name])

        ninja.newline()
        for name in sorted(self.overrides):
            for num, val in sorted((num, val) for (val, num) in self.overrides[name].iteritems()):
                ninja.variable('%s_%s'%(name, num), val)

    def write_rules(self, ninja):
        ninja.newline()

        ninja.rule('EXEC', command='$command')
        ninja.rule('EXEC_RSP',
                command='$command',
                rspfile = '$out.rsp',
                rspfile_content = '$in',
                )

        ninja.rule('INSTALL',
                command = '$COPY $in $out',
                description = 'INSTALL $out')

        ninja.rule('SCRIPT_RSP',
            command = '$PYTHON $script $in $out $out.rsp',
            rspfile = '$out.rsp',
            rspfile_content = '$rspfile_content',
            restat = 1,
            description = "GEN $out")
        ninja.rule('SCONS',
            command = '$PYTHON %s -Q $scons_args $out'%(sys.argv[0]),
            pool = 'console',
            description = 'SCONSGEN $out',
            restat=1)

        if self.globalEnv.ToolchainIs('gcc', 'clang'):
            # ninja ignores leading spaces so this will work fine if empty.
            ccache = self.globalEnv.get('_NINJA_CCACHE', '')
            if 'CXX' in self.tool_commands:
                ninja.rule('CXX',
                    deps = 'gcc',
                    depfile = '$out.d',
                    command = '%s %s -MMD -MF $out.d'%(ccache, self.tool_commands['CXX']),
                    description = 'CXX $out')
            if 'SHCXX' in self.tool_commands:
                ninja.rule('SHCXX',
                    deps = 'gcc',
                    depfile = '$out.d',
                    command = '%s %s -MMD -MF $out.d'%(ccache, self.tool_commands['SHCXX']),
                    description = 'SHCXX $out')
            if 'CC' in self.tool_commands:
                ninja.rule('CC',
                    deps = 'gcc',
                    depfile = '$out.d',
                    command = '%s %s -MMD -MF $out.d'%(ccache, self.tool_commands['CC']),
                    description = 'CC $out')
            if 'SHCC' in self.tool_commands:
                ninja.rule('SHCC',
                    deps = 'gcc',
                    depfile = '$out.d',
                    command = '%s %s -MMD -MF $out.d'%(ccache, self.tool_commands['SHCC']),
                    description = 'SHCC $out')
            if 'SHLINK' in self.tool_commands:
                ninja.rule('SHLINK',
                    command = '$SHLINK @$out.rsp',
                    rspfile = '$out.rsp',
                    rspfile_content = self.tool_commands['SHLINK'].replace('$SHLINK ', ''),
                    description = 'DYNLIB $out')
            if 'LINK' in self.tool_commands:
                ninja.rule('LINK',
                    command = '$LINK @$out.rsp',
                    rspfile = '$out.rsp',
                    rspfile_content = self.tool_commands['LINK'].replace('$LINK ', ''),
                    description = 'LINK $out')
            if 'AR' in self.tool_commands:
                # We need to remove $out because the file existing can confuse ar. This is particularly
                # a problem when switching between thin and non-thin archive files.
                ninja.rule('AR',
                    command = 'rm -f $out && ' + self.tool_commands['AR'],
                    description = 'STATICLIB $out')
        else:
            for tool in self.tool_commands:
                assert not tool.startswith('SH')

            if 'CXX' in self.tool_commands:
                ninja.rule('CXX',
                    deps = 'msvc',
                    command = '%s /showIncludes'%(self.tool_commands['CXX']),
                    description = 'CXX $out')
            if 'CC' in self.tool_commands:
                ninja.rule('CC',
                    deps = 'msvc',
                    command = '%s /showIncludes'%(self.tool_commands['CC']),
                    description = 'CC $out')
            if 'RC' in self.tool_commands:
                ninja.rule('RC',
                    command = self.tool_commands['RC'],
                    description = 'RC $out')
            if 'LINK' in self.tool_commands:
                ninja.pool('winlink', GetOption('link-pool-depth'))
                ninja.rule('LINK',
                    command = 'cmd /c $PYTHON %s $out.rsp && $LINK @$out.rsp'%split_lines_script,
                    rspfile = '$out.rsp',
                    rspfile_content = self.tool_commands['LINK'].replace('$LINK ', ''),
                    pool='winlink',
                    description = 'LINK $out')


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
        deps = SCons.Util.flatten([
            'SConstruct',
            rglob('SConscript', 'src'),
            rglob('*.py', 'site_scons'),
            rglob('*.py', 'buildscripts'),
            rglob('*.py', 'src/third_party/scons-2.5.0'),
            rglob('*.py', 'src/mongo/db/modules'),
            [self.globalEnv.WhereIs(tool) for tool in self.tool_paths],
            self.rc_files, # We rely on scons to tell us the deps of windows rc files.

            # Depend on git as position as well. This ensures that error codes are always checked
            # after rebase.
            '.git/HEAD',
            glob.glob('.git/refs/heads/*'),
            ])
        deps = sorted(set(dep.replace(' ', '\\ ')
                          for dep in deps
                          if dep and os.path.isfile(dep)))

        depfile = self.ninja_file + '.deps'
        with file(depfile, 'w') as f:
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
        print "*** To prevent PEBKACs, the ninja module requires that you pass a target to scons."
        print "*** You probably forgot to include build.ninja on the command line"
        print "***"
        print "*** If you really want to build mongod using scons, do so explicitly or pass the"
        print "*** --modules= flag to disable the ninja module."
        Exit(1)

    ninja_files = [str(t) for t in BUILD_TARGETS if str(t).endswith('.ninja')]
    if not ninja_files:
        return

    try:
        print "Checking for updates to ninja module..."
        subprocess.check_call(['git', '-C', my_dir, 'fetch'])
        output = subprocess.check_output(['git', '-C', my_dir, 'log', '--oneline', '@..origin'])
        if output:
            print "***"
            print "*** Your ninja module is out of date. New commits:"
            print "***"
            print output
    except Exception, e:
        # Errors checking for updates shouldn't prevent building.
        print "Ignoring error checking for updates: ", e

    if 'ninja' not in env.subst("$VARIANT_DIR"):
        print "*** WARNING: you should use a dedicated VARIANT_DIR for ninja builds to prevent"
        print "*** conflicts with scons builds. You can suppress this by including 'ninja'"
        print "*** in your VARIANT_DIR string."
        print '***'

    if GetOption('cache'):
        print "*** Remove --cache flags to make ninja generation work."
        print "*** ccache is used automatically if it is installed."
        Exit(1)

    env['NINJA'] = where_is(env, 'ninja')
    if not env['NINJA']:
        env['NINJA'] = where_is(env, 'ninja-build') # Fedora...

    action_str = "Generating $TARGET"
    if env.ToolchainIs('gcc', 'clang'):
        # ninja buffers stdout which causes gcc and clang not to emit color. Force it on and let
        # ninja filter out the colors if the real stdout is redirected.
        env.Append(CCFLAGS=["-fdiagnostics-color=always"])

        using_gsplitdwarf = any('-gsplit-dwarf' in env[var]
                                for var in ('CCFLAGS', 'CFLAGS', 'CXXFLAGS'))

        if using_gsplitdwarf and not env.TargetOSIs('linux'):
            print "*** -gsplit-dwarf is only supported on Linux."
            Exit(1)

        if GetOption('cache_disable'):
            env['_NINJA_CCACHE'] = ''
        else:
            env['_NINJA_CCACHE'] = where_is(env, 'ccache')
        if env['_NINJA_CCACHE']:
            action_str += " with ccache support (pass --no-cache to scons to disable)"
            if env.ToolchainIs('clang'):
                # Needed to make clang++ play nicely with ccache. Ideally this would use
                # AddToCCFLAGSIfSupported but that is available to modules.
                env.Append(CCFLAGS=["-Qunused-arguments"])

            settings = subprocess.check_output([env['_NINJA_CCACHE'], '--print-config'])
            if 'max_size = 5.0G' in settings:
                print '*** ccache is using the default 5GB cache size. You can raise it by running:'
                print '*** ccache -o max_size=20G'
                print '***'

            if 'run_second_cpp = false' in settings:
                # This defaults to true in new versions. Our codebase generates spurious warnings
                # when it is false because the compiler can't see what is part of a macro expansion.
                print '*** Change the ccache run_second_cpp flag to true by running:'
                print '*** ccache -o run_second_cpp=true'
                print '***'
                Exit(1)

            if using_gsplitdwarf:
                version = (subprocess.check_output([env['_NINJA_CCACHE'], '--version'])
                                     .split('\n', 1)[0]
                                     .split()[-1])
                if map(int, version.split('.')) < [3, 2, 3]:
                    print "*** -gsplit-dwarf requires ccache >= 3.2.3. You have: " + version
                    Exit(1)

            if env["MONGO_VERSION"] != "0.0.0" or env["MONGO_GIT_HASH"] != "unknown":
                print "*** WARNING: to get the most out of ccache, pass these flags to scons:"
                print '*** MONGO_VERSION="0.0.0" MONGO_GIT_HASH="unknown"'
                print '***'

    for ninja_file in ninja_files:
        cmd = env.Command(ninja_file, [], Action(makeNinjaFile, action_str))
        env.NoCache(cmd)
        env.AlwaysBuild(cmd)
