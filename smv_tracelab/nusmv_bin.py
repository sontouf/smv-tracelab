"""Locate a NuSMV executable, or download the official FBK binary on first use."""
import io
import os
import platform
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
        raise RuntimeError('이 플랫폼(%s/%s)용 공식 NuSMV 바이너리가 없습니다. '
                           '소스(https://nusmv.fbk.eu)에서 빌드한 뒤 --nusmv 로 지정하세요.'
                           % (sys.platform, platform.machine()))
    url = BASE_URL + name
    dest = cache_dir() / ('NuSMV-' + NUSMV_VERSION)
    log('NuSMV %s 다운로드: %s' % (NUSMV_VERSION, url))
    log('  (NuSMV 는 FBK-irst 의 LGPL-2.1 소프트웨어입니다. https://nusmv.fbk.eu)')
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
        raise RuntimeError('다운로드한 아카이브에서 %s 를 찾지 못했습니다.' % EXE)
    os.chmod(exe, 0o755)
    log('  설치됨: %s' % exe)
    return exe


LIB_HINT = ('NuSMV 실행에 필요한 공유 라이브러리가 없습니다: %s\n'
            '  Debian/Ubuntu: sudo apt install libxml2 libedit2\n'
            '  Fedora/RHEL  : sudo dnf install libxml2 libedit')


def _missing_libs(exe, env):
    try:
        out = subprocess.run(['ldd', exe], capture_output=True, text=True, env=env, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split()[0] for line in out.splitlines() if 'not found' in line]


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
