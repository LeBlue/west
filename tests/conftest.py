# Copyright (c) 2019, Nordic Semiconductor ASA
#
# SPDX-License-Identifier: Apache-2.0

import os
import platform
import shlex
import shutil
import subprocess
import sys
import textwrap

from west import configuration as config
import pytest

GIT = shutil.which('git')
MANIFEST_TEMPLATE = '''\
manifest:
  defaults:
    remote: test-local

  remotes:
    - name: test-local
      url-base: THE_URL_BASE

  projects:
    - name: Kconfiglib
      revision: zephyr
      path: subdir/Kconfiglib
    - name: net-tools
      west-commands: scripts/west-commands.yml
  self:
    path: zephyr
'''

#
# Test fixtures
#

@pytest.fixture(scope='session')
def _session_repos():
    '''Just a helper, do not use directly.'''

    # It saves time to create repositories once at session scope, then
    # clone the results as needed in per-test fixtures.
    session_repos = os.path.join(os.environ['TOXTEMPDIR'], 'session_repos')
    print('initializing session repositories in', session_repos)
    shutil.rmtree(session_repos, ignore_errors=True)

    # Create the repositories.
    rp = {}      # individual repository paths
    for repo in 'net-tools', 'Kconfiglib', 'zephyr':
        path = os.path.join(session_repos, repo)
        rp[repo] = path
        create_repo(path)

    # Initialize the "zephyr" repository.
    # The caller needs to add west.yml with the right url-base.
    add_commit(rp['zephyr'], 'base zephyr commit',
               files={'CODEOWNERS': '',
                      'include/header.h': '#pragma once\n',
                      'subsys/bluetooth/code.c': 'void foo(void) {}\n'})

    # Initialize the Kconfiglib repository.
    subprocess.check_call([GIT, 'checkout', '-b', 'zephyr'],
                          cwd=rp['Kconfiglib'])
    add_commit(rp['Kconfiglib'], 'test kconfiglib commit',
               files={'kconfiglib.py': 'print("hello world kconfiglib")\n'})

    # Initialize the net-tools repository.
    add_commit(rp['net-tools'], 'test net-tools commit',
               files={'qemu-script.sh': 'echo hello world net-tools\n',
                      'scripts/west-commands.yml': textwrap.dedent('''\
                      west-commands:
                        - file: scripts/test.py
                          commands:
                            - name: test
                              class: Test
                              help: test-help
                      '''),
                      'scripts/test.py': textwrap.dedent('''\
                      from west.commands import WestCommand
                      class Test(WestCommand):
                          def __init__(self):
                              super(Test, self).__init__(
                                  'test',
                                  'test application',
                                  '')
                          def do_add_parser(self, parser_adder):
                              parser = parser_adder.add_parser(self.name)
                              return parser
                          def do_run(self, args, ignored):
                              print('Testing test command 1')
                      '''),
                      })

    # Return the top-level temporary directory. Don't clean it up on
    # teardown, so the contents can be inspected post-portem.
    print('finished initializing session repositories')
    return session_repos

@pytest.fixture
def repos_tmpdir(tmpdir, _session_repos):
    '''Fixture for tmpdir with "remote" repositories.

    These can then be used to bootstrap an installation and run
    project-related commands on it with predictable results.

    Switches directory to, and returns, the top level tmpdir -- NOT
    the subdirectory containing the repositories themselves.

    Initializes placeholder upstream repositories in tmpdir with the
    following contents:

    repos/
    ├── Kconfiglib (branch: zephyr)
    │   └── kconfiglib.py
    ├── net-tools (branch: master)
    │   └── qemu-script.sh
    └── zephyr (branch: master)
        ├── CODEOWNERS
        ├── west.yml
        ├── include
        │   └── header.h
        └── subsys
            └── bluetooth
                └── code.c

    The contents of west.yml are:

    manifest:
      defaults:
        remote: test-local
      remotes:
        - name: test-local
          url-base: <tmpdir>/repos
      projects:
        - name: Kconfiglib
          revision: zephyr
          path: subdir/Kconfiglib
        - name: net-tools
          clone_depth: 1
          west-commands: scripts/west-commands.yml
      self:
        path: zephyr

    '''
    kconfiglib, net_tools, zephyr = [os.path.join(_session_repos, x) for x in
                                     ['Kconfiglib', 'net-tools', 'zephyr']]
    repos = tmpdir.mkdir('repos')
    repos.chdir()
    for r in [kconfiglib, net_tools, zephyr]:
        subprocess.check_call([GIT, 'clone', r])

    manifest = MANIFEST_TEMPLATE.replace('THE_URL_BASE',
                                         str(tmpdir.join('repos')))
    add_commit(str(repos.join('zephyr')), 'add manifest',
               files={'west.yml': manifest})
    return tmpdir

@pytest.fixture
def west_init_tmpdir(repos_tmpdir):
    '''Fixture for a tmpdir with 'remote' repositories and 'west init' run.

    Uses the remote repositories from the repos_tmpdir fixture to
    create a west installation using the system bootstrapper's init
    command.

    The contents of the west installation aren't checked at all.
    This is left up to the test cases.

    The directory that 'west init' created is returned as a
    py.path.local, with the current working directory set there.'''
    west_tmpdir = repos_tmpdir.join('west_installation')
    cmd('init -m "{}" "{}"'.format(str(repos_tmpdir.join('repos', 'zephyr')),
                                   str(west_tmpdir)))
    west_tmpdir.chdir()
    config.read_config()
    return west_tmpdir

#
# Helper functions
#

def check_output(*args, **kwargs):
    # Like subprocess.check_output, but returns a string in the
    # default encoding instead of a byte array.
    try:
        out_bytes = subprocess.check_output(*args, **kwargs)
    except subprocess.CalledProcessError as e:
        print('*** check_output: nonzero return code', e.returncode,
              file=sys.stderr)
        print('cwd =', os.getcwd(), 'args =', args,
              'kwargs =', kwargs, file=sys.stderr)
        print('subprocess output:', file=sys.stderr)
        print(e.output.decode(), file=sys.stderr)
        raise
    return out_bytes.decode(sys.getdefaultencoding())

def cmd(cmd, cwd=None, stderr=None, env=None):
    # Run a west command in a directory (cwd defaults to os.getcwd()).
    #
    # This helper takes the command as a string.
    #
    # This helper relies on the test environment to ensure that the
    # 'west' executable is a bootstrapper installed from the current
    # west source code.
    #
    # stdout from cmd is captured and returned. The command is run in
    # a python subprocess so that program-level setup and teardown
    # happen fresh.
    cmd = 'west ' + cmd
    if platform.system() != 'Windows':
        cmd = shlex.split(cmd)
    print('running:', cmd)
    if env:
        print('with non-default environment:')
        for k in env:
            if k not in os.environ or env[k] != os.environ[k]:
                print('\t{}={}'.format(k, env[k]))
        for k in os.environ:
            if k not in env:
                print('\t{}: deleted, was: {}'.format(k, os.environ[k]))
    try:
        return check_output(cmd, cwd=cwd, stderr=stderr, env=env)
    except subprocess.CalledProcessError:
        print('cmd: west:', shutil.which('west'), file=sys.stderr)
        raise

def create_repo(path):
    # Initializes a Git repository in 'path', and adds an initial commit to it

    subprocess.check_call([GIT, 'init', path])

    config_repo(path)
    add_commit(path, 'initial')


def config_repo(path):
    # Set name and email. This avoids a "Please tell me who you are" error when
    # there's no global default.
    subprocess.check_call([GIT, 'config', 'user.name', 'West Test'], cwd=path)
    subprocess.check_call([GIT, 'config', 'user.email',
                           'west-test@example.com'],
                          cwd=path)


def add_commit(repo, msg, files=None, reconfigure=True):
    # Adds a commit with message 'msg' to the repo in 'repo'
    #
    # If 'files' is given, it must be a dictionary mapping files to
    # edit to the contents they should contain in the new
    # commit. Otherwise, the commit will be empty.
    #
    # If 'reconfigure' is True, the user.name and user.email git
    # configuration variables will be set in 'repo' using config_repo().
    repo = str(repo)

    if reconfigure:
        config_repo(repo)

    # Edit any files as specified by the user and add them to the index.
    if files:
        for path, contents in files.items():
            dirname, basename = os.path.dirname(path), os.path.basename(path)
            fulldir = os.path.join(repo, dirname)
            if not os.path.isdir(fulldir):
                # Allow any errors (like trying to create a directory
                # where a file already exists) to propagate up.
                os.makedirs(fulldir)
            with open(os.path.join(fulldir, basename), 'w') as f:
                f.write(contents)
            subprocess.check_call([GIT, 'add', path], cwd=repo)

    # The extra '--no-xxx' flags are for convenience when testing
    # on developer workstations, which may have global git
    # configuration to sign commits, etc.
    #
    # We don't want any of that, as it could require user
    # intervention or fail in environments where Git isn't
    # configured.
    subprocess.check_call(
        [GIT, 'commit', '-a', '--allow-empty', '-m', msg, '--no-verify',
         '--no-gpg-sign', '--no-post-rewrite'], cwd=repo)


def rev_parse(repo, revision):
    out = subprocess.check_output([GIT, 'rev-parse', revision], cwd=repo)
    return out.decode(sys.getdefaultencoding())
