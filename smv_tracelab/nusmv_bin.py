"""Locate a NuSMV executable, or download the official FBK binary on first use."""
import io
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

NUSMV_VERSION = '2.7.1'
BASE_URL = 'https://nusmv.fbk.eu/distrib/%s/' % NUSMV_VERSION
EXE = 'NuSMV.exe' if sys.platform == 'win32' else 'NuSMV'


def cache_dir():
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Caches'
    else:
        base = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache')
    return base / 'smv-tracelab'


def _archive_name():
    machine = platform.machine().lower()
    if sys.platform == 'win32' and machine in ('amd64', 'x86_64', 'arm64'):
        return 'NuSMV-%s-win64.zip' % NUSMV_VERSION
    if sys.platform == 'darwin':
        return 'NuSMV-%s-macos-universal.tar.xz' % NUSMV_VERSION
    if sys.platform.startswith('linux') and machine in ('x86_64', 'amd64'):
        return 'NuSMV-%s-linux64.tar.xz' % NUSMV_VERSION
    return None


def cached_binary():
    root = cache_dir() / ('NuSMV-' + NUSMV_VERSION)
    if root.is_dir():
        for p in root.rglob(EXE):
            if p.parent.name == 'bin' and p.is_file():
                return str(p)
    return None


def find_nusmv(explicit=None):
    """--nusmv > $NUSMV > PATH > previously downloaded binary."""
    for c in (explicit, os.environ.get('NUSMV'), shutil.which('NuSMV'), shutil.which('nusmv'), cached_binary()):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def download_nusmv(log=print):
    name = _archive_name()
    if not name:
        raise RuntimeError('there is no official NuSMV binary for %s/%s; build it from source '
                           '(https://nusmv.fbk.eu) and pass --nusmv' % (sys.platform, platform.machine()))
    url = BASE_URL + name
    dest = cache_dir() / ('NuSMV-' + NUSMV_VERSION)
    log('Downloading NuSMV %s: %s' % (NUSMV_VERSION, url))
    log('  (NuSMV is LGPL-2.1 software by FBK-irst, https://nusmv.fbk.eu)')
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    if name.endswith('.zip'):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            members = [m for m in z.namelist() if '/bin/' in m]
            z.extractall(dest, members=members)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:xz') as t:
            members = [m for m in t.getmembers() if '/bin/' in m.name and (m.isfile() or m.isdir())]
            for m in members:
                if m.name.startswith('/') or '..' in Path(m.name).parts:
                    raise RuntimeError('unsafe archive member: %s' % m.name)
            kw = {'filter': 'data'} if hasattr(tarfile, 'data_filter') else {}
            t.extractall(dest, members=members, **kw)
    exe = cached_binary()
    if not exe:
        raise RuntimeError('%s not found in the downloaded archive' % EXE)
    os.chmod(exe, 0o755)
    log('  installed: %s' % exe)
    return exe


if sys.platform == 'darwin':
    LIB_HINT = ('NuSMV needs shared libraries that are missing: %s\n'
                '  Homebrew: brew install gmp libedit\n'
                '  MacPorts: sudo port install gmp libedit libxml2')
else:
    LIB_HINT = ('NuSMV needs shared libraries that are missing: %s\n'
                '  Debian/Ubuntu: sudo apt install libxml2 libedit2\n'
                '  Fedora/RHEL  : sudo dnf install libxml2 libedit')

# The official macOS build links against MacPorts paths (/opt/local/lib/libedit.0.dylib, libgmp.10.dylib,
# libxml2.2.dylib). dyld retries the leaf name in DYLD_FALLBACK_LIBRARY_PATH, so Homebrew copies work too.
DARWIN_LIB_DIRS = ['/opt/homebrew/opt/libedit/lib', '/opt/homebrew/opt/gmp/lib', '/opt/homebrew/opt/libxml2/lib',
                   '/opt/homebrew/lib', '/usr/local/opt/libedit/lib', '/usr/local/opt/gmp/lib',
                   '/usr/local/opt/libxml2/lib', '/usr/local/lib', '/opt/local/lib', '/usr/lib']


def _missing_libs(exe, env):
    try:
        out = subprocess.run(['ldd', exe], capture_output=True, text=True, env=env, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split()[0] for line in out.splitlines() if 'not found' in line]


def _missing_darwin(exe, env):
    try:
        r = subprocess.run([exe, '-h'], capture_output=True, text=True, env=env, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    return [os.path.basename(m) for m in re.findall(r'Library not loaded: (\S+)', r.stdout + r.stderr)]


def _find_system_lib(prefix):
    try:
        out = subprocess.run(['ldconfig', '-p'], capture_output=True, text=True, timeout=20).stdout
        for line in out.splitlines():
            name, _, path = line.strip().partition(' => ')
            if name.startswith(prefix) and os.path.exists(path):
                return path
    except (OSError, subprocess.SubprocessError):
        pass
    for d in ('/usr/lib/x86_64-linux-gnu', '/lib/x86_64-linux-gnu', '/usr/lib64', '/usr/lib'):
        hits = sorted(Path(d).glob(prefix + '*')) if os.path.isdir(d) else []
        if hits:
            return str(hits[-1])
    return None


def runtime_env(exe):
    """Environment for running `exe`, plus the shared libraries that are still missing.

    The official Linux build links against libedit.so.0, while Debian/Ubuntu ship libedit.so.2
    (same API). If only the soname differs, a symlink in a private directory on LD_LIBRARY_PATH fixes it.
    """
    env = dict(os.environ)
    if sys.platform == 'darwin':
        dirs = [d for d in DARWIN_LIB_DIRS if os.path.isdir(d)]
        env['DYLD_FALLBACK_LIBRARY_PATH'] = os.pathsep.join(
            [p for p in (env.get('DYLD_FALLBACK_LIBRARY_PATH'),) if p] + dirs)
        return env, _missing_darwin(exe, env)
    if not sys.platform.startswith('linux'):
        return env, []
    missing = _missing_libs(exe, env)
    if 'libedit.so.0' in missing:
        target = _find_system_lib('libedit.so.')
        if target:
            shim = cache_dir() / 'libshim'
            shim.mkdir(parents=True, exist_ok=True)
            link = shim / 'libedit.so.0'
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(target)
            env['LD_LIBRARY_PATH'] = os.pathsep.join(p for p in (str(shim), env.get('LD_LIBRARY_PATH')) if p)
            missing = _missing_libs(exe, env)
    return env, missing
