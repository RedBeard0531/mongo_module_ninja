import SCons
from SCons.Script import *

import os
import re
import sys
import glob
import shlex
import ninja_syntax
import fnmatch

def makeNinjaFile(target, source, env):
    assert not source
    ninja_file = NinjaFile(str(target[0]), env)
    ninja_file.write()

def rglob(pattern, root='.') :
    return [os.path.join(path, f)
            for path, dirs, files in os.walk(root)
            for f in fnmatch.filter(files, pattern)]

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
        self.scons_generated_header_builds = []

        self.find_aliases()
        self.find_build_nodes()

    def find_aliases(self):
        for alias in SCons.Node.Alias.default_ans.values():
            if alias.get_builder() == SCons.Environment.AliasBuilder:
                # "pure" aliases
                self.aliases[str(alias)] = [str(s) for s in alias.sources]
                pass
            else:
                # Ignore these for now
                assert (str(alias) in ('dist', 'lint'))

    def find_build_nodes(self):
        seen = set()
        for n in self.globalEnv.fs.Top.root._lookupDict.values():
            if not SCons.Node.is_derived_node(n): continue
            if isinstance(n, SCons.Node.FS.Dir): continue
            if str(n.executor).startswith('write_uuid_to_file('): continue
            if '/sconf_temp/conftest' in str(n): continue
            if str(n).startswith('build/install/'): continue

            # We see each build task once per target, but we handle all targets the first time.
            if id(n.executor) not in seen:
                seen.add(id(n.executor))
                self.handle_build_node(n)

        # Group all scons generated headers into a single scons run. Since all must finish before
        # compiling starts, we want it to be as fast as possible so we only pay the scons startup
        # cost once.
        # TODO use ninja batching if it is ever implemented.
        scons_generated_header_build = dict(rule='SCONS')
        for build in self.scons_generated_header_builds:
            for field in build:
                if field == 'rule':
                    assert build[field] == 'SCONS'
                else:
                    scons_generated_header_build.setdefault(field, set()).update(build[field])
        for field in scons_generated_header_build:
            if field != 'rule':
                scons_generated_header_build[field] = sorted(scons_generated_header_build[field])
        self.builds.append(scons_generated_header_build)

        for build in self.builds:
            # Make everything build by scons depend on the ninja file. This makes them transitively
            # depend on all of the scons dependencies so scons gets a chance to rebuild them
            # whenever any scons files change.
            if build['rule'] == 'SCONS':
                build.setdefault('implicit', []).append(self.ninja_file)

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

        generating_header = False
        for target in targets:
            if target.always_build:
                implicit_deps.append('_ALWAYS_BUILD')

            target = str(target)
            # This is handled explicitly in write_regenerator.
            if target.endswith('.ninja'): return

            self.built_targets.add(target)
            if target.endswith('.h') or target.endswith('.hpp'):
                self.generated_headers.add(target)
                generating_header = True

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

        # Remove the noop_action we use to fake ranlib with thin archives.
        if isinstance(action, SCons.Action.ListAction) and len(action.list) == 2:
            if str(n.executor).split('\n')[1].startswith('noop_action'):
                n.executor.set_action_list(action.list[0])
                assert len(n.executor.get_action_list()) == 1
                action = n.executor.get_action_list()[0]

        isDistScr = len(str(n.executor).split('\n')) > 1 # currently just DistScr stuff
        if isinstance(action, SCons.Action.FunctionAction) or isDistScr:
            # Build all scons generated headers in a single pass.
            build_list = (self.builds
                          if not generating_header
                          else self.scons_generated_header_builds)

            sources = filter(lambda s: not isinstance(s, SCons.Node.Python.Value), sources)
            build_list.append(dict(
                rule='SCONS',
                outputs=strmap(targets),
                inputs=strmap(sources),
                implicit=implicit_deps
                ))

            return

        assert isinstance(action, (SCons.Action.CommandAction, SCons.Action.CommandGeneratorAction))

        tool = str(n.executor).split(None, 1)[0]
        if tool not in ('$CC', '$CXX', '$LINK', '$AR'):
            self.builds.append(dict(
                rule='EXEC',
                outputs=strmap(targets),
                inputs=strmap(sources),
                implicit=implicit_deps,
                variables={'command': myEnv.subst(str(n.executor), executor=n.executor)}
                ))
            return

        self.tool_paths.add(myEnv.WhereIs(tool))

        tool = tool.strip('${}')
        com = str(n.executor).replace('$TARGET', '$out').replace('$SOURCES', '$in')
        if tool in self.tool_commands:
            assert com.replace('$$', '$') == self.tool_commands[tool]
        else:
            self.tool_commands[tool] = com.replace('$$', '$')

        libdeps = []
        if tool == 'LINK':
            libdeps_objs = myEnv.subst('$_LIBDEPS',executor=n.executor)
            n.executor.get_lvars()['_LIBDEPS'] = libdeps_objs # cache the result.
            libdeps = libdeps_objs.split()

        myVars = {}
        for word in shlex.split(com):
            if not word.startswith('$'): continue
            if word in ('$in', '$out'): continue

            name = word.strip('${}')
            #TODO find some way to make sure they aren't used elsewhere in the rule
            assert not name.startswith('TARGET')
            assert not name.startswith('SOURCE')
            assert re.match(r'^[a-zA-Z_]*$', name)

            mySubst = myEnv.subst(word, executor=n.executor)
            if name == '_LIBFLAGS':
                # never worth commoning since it includes libdeps.
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

        self.builds.append(dict(
            rule=tool,
            outputs=strmap(targets),
            inputs=strmap(sources),
            implicit=implicit_deps + libdeps + [myEnv.WhereIs('$'+tool)],
            order_only='_generated_headers' if tool in ('CC', 'CXX') else None,
            variables=myVars,
            ))


    def write(self):
        file = open(self.ninja_file, 'w')

        # make ninja file directly executable. (bit set later)
        # can't use ninja.comment() because it adds a space after the !
        file.write('#!%s -f\n'%self.globalEnv.WhereIs('ninja'))

        ninja = ninja_syntax.Writer(file, width=100)

        ninja.newline()
        ninja.comment('Generated by scons. DO NOT EDIT.')

        self.write_vars(ninja)
        self.write_rules(ninja)
        self.write_builds(ninja)
        self.write_regenerator(ninja)

        ninja.newline()
        for default in sorted(strmap(DEFAULT_TARGETS)):
            ninja.default(default)

        ninja.close()
        if not self.globalEnv.TargetOSIs('windows'):
            os.chmod(self.ninja_file, 0755)

    def write_vars(self, ninja):
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

        # ninja ignores leading spaces so this will work fine if empty.
        ccache = self.globalEnv['_NINJA_CCACHE']

        #TODO windows
        ninja.rule('SCONS',
            command = '%s %s -Q $scons_args $out'%(self.globalEnv.WhereIs('$PYTHON'), sys.argv[0]),
            pool = 'console',
            description = 'SCONSGEN $out',
            restat=1)
        ninja.rule('CXX',
            deps = 'gcc',
            depfile = '$out.d',
            command = '%s %s -MMD -MF $out.d'%(ccache, self.tool_commands['CXX']),
            description = 'CXX $out')
        ninja.rule('CC',
            deps = 'gcc',
            depfile = '$out.d',
            command = '%s %s -MMD -MF $out.d'%(ccache, self.tool_commands['CC']),
            description = 'CC $out')
        ninja.rule('LINK',
            command = self.tool_commands['LINK'],
            description = 'LINK $out')
        ninja.rule('INSTALL', command = 'install $in $out') # install seems faster than cp.
        ninja.rule('EXEC', command='$command')

        if 'AR' in self.tool_commands:
            ninja.rule('AR',
                command = self.tool_commands['AR'],
                description = 'AR $out')

    def write_builds(self, ninja):
        ninja.newline()
        for build in self.builds:
            ninja.build(**build)

        ninja.newline()
        for alias in sorted(self.aliases):
            if alias not in self.built_targets:
                # For some reason we sometimes define a task then alias it to itself.
                ninja.build(alias, 'phony', strmap(self.aliases[alias]))

        ninja.newline()
        ninja.build('_generated_headers', 'phony', sorted(self.generated_headers))
        ninja.build('_ALWAYS_BUILD', 'phony')

    def write_regenerator(self, ninja):
        scons_dependencies = sorted(set(SCons.Util.flatten([
            rglob('SCons*'),
            rglob('*.py', 'site_scons'),
            rglob('*.py', 'buildscripts'),
            rglob('*.py', 'src/third_party/scons-2.5.0'),
            rglob('*.py', 'src/mongo/db/modules'),
            [self.globalEnv.WhereIs(tool) for tool in self.tool_paths],
            ])))

        ninja.newline()
        ninja.rule('GENERATOR',
            command = "%s %s $scons_args $out"%(self.globalEnv.WhereIs('$PYTHON'), sys.argv[0]),
            pool = 'console',
            generator = 1,
            description = 'Regenerating $out',
            restat=1)
        ninja.build(self.ninja_file, 'GENERATOR',
                implicit=['buildscripts/scons.py'] + scons_dependencies)

def configure(conf, env):
    ninja_files = [str(t) for t in BUILD_TARGETS if str(t).endswith('.ninja')]
    if not ninja_files:
        return

    if GetOption('cache'):
        print "Remove --cache flags to make ninja generation work."
        print "ccache is used automatically if it is installed."
        Exit(1)

    action_str = "Generating $TARGET"
    env['_NINJA_CCACHE'] = '' if GetOption('cache_disable') else env.WhereIs('ccache')
    if env['_NINJA_CCACHE']:
        action_str += " with ccache support (pass --no-cache to scons to disable)"
        if env.ToolchainIs('clang'):
            # Needed to make clang++ play nicely with ccache. Ideally this would use
            # AddToCCFLAGSIfSupported but that is available to modules.
            env.Append(CCFLAGS=["-Qunused-arguments"])

    if env.ToolchainIs('gcc', 'clang'):
        # ninja buffers stdout which causes gcc and clang not to emit color. Force it on and let
        # ninja filter out the colors if the real stdout is redirected.
        env.Append(CCFLAGS=["-fdiagnostics-color=always"])

    for ninja_file in ninja_files:
        cmd = env.Command(ninja_file, [], Action(makeNinjaFile, action_str))
        env.NoCache(cmd)
        env.AlwaysBuild(cmd)
