"""Run NuSMV attached to a pseudo-terminal.

NuSMV fully buffers stdout when it is connected to a pipe, so the
"Choose a state" listing of `simulate -i` would only appear *after* we answer it.
A pty (POSIX) or ConPTY via pywinpty (Windows) makes NuSMV line-buffer its output.
"""
import os
import subprocess
import sys
import threading


class _Base:
    def __init__(self):
        self._buf = []
        self._cond = threading.Condition()
        self._eof = False

    def _pump(self):
        while True:
            try:
                data = self._read_chunk()
            except (OSError, EOFError):
                data = None
            with self._cond:
                if not data:
                    self._eof = True
                    self._cond.notify_all()
                    return
                self._buf.append(data)
                self._cond.notify_all()

    def _start_pump(self):
        threading.Thread(target=self._pump, daemon=True).start()

    def read(self, timeout):
        """Return output collected so far, waiting up to `timeout` s for something to arrive."""
        with self._cond:
            if not self._buf and not self._eof:
                self._cond.wait(timeout)
            data, self._buf = ''.join(self._buf), []
            return data

    @property
    def eof(self):
        return self._eof


class PosixPty(_Base):
    def __init__(self, argv, cwd, env=None):
        import pty
        import termios
        super().__init__()
        master, slave = pty.openpty()
        attr = termios.tcgetattr(slave)
        attr[3] &= ~(termios.ECHO | termios.ICANON)
        termios.tcsetattr(slave, termios.TCSANOW, attr)
        self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, cwd=cwd,
                                     env=env, close_fds=True, start_new_session=True)
        os.close(slave)
        self.fd = master
        self._start_pump()

    def _read_chunk(self):
        return os.read(self.fd, 65536).decode('utf-8', 'replace')

    def write(self, text):
        os.write(self.fd, text.encode())

    def alive(self):
        return self.proc.poll() is None

    def kill(self):
        import signal
        if self.alive():
            try:
                self.write('quit\n')
                self.proc.wait(timeout=1.5)
            except Exception:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except Exception:
                    pass
        try:
            os.close(self.fd)
        except OSError:
            pass


class WinPty(_Base):
    def __init__(self, argv, cwd, env=None):
        try:
            from winpty import PtyProcess
        except ImportError:
            raise RuntimeError('Windows 에서는 pywinpty 가 필요합니다: pip install pywinpty')
        super().__init__()
        # 넓은 터미널로 만들어 ConPTY 의 줄바꿈 삽입을 피한다.
        self.proc = PtyProcess.spawn(subprocess.list2cmdline(argv), cwd=cwd, env=env, dimensions=(50, 4000))
        self._start_pump()

    def _read_chunk(self):
        return self.proc.read(65536)

    def write(self, text):
        self.proc.write(text.replace('\n', '\r\n'))

    def alive(self):
        return self.proc.isalive()

    def kill(self):
        if self.alive():
            try:
                self.write('quit\n')
            except Exception:
                pass
            try:
                self.proc.terminate(force=True)
            except Exception:
                pass


def spawn(argv, cwd, env=None):
    return (WinPty if sys.platform == 'win32' else PosixPty)(argv, cwd, env)
