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
                self._ingest(data)
                self._cond.notify_all()

    def _ingest(self, data):
        self._buf.append(data)

    def _start_pump(self):
        threading.Thread(target=self._pump, daemon=True).start()

    def read(self, timeout):
        """Return output collected so far, waiting up to `timeout` s for something to arrive."""
        with self._cond:
            if not self._buf and not self._eof:
                self._cond.wait(timeout)
            data, self._buf = ''.join(self._buf), []
            return data

    def partial(self):
        """Text of the line still being written (only non-empty for screen-based terminals)."""
        return ''

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


class ScreenText:
    """Turns a terminal *screen* stream back into plain text.

    ConPTY does not pass the program's bytes through: it repaints a virtual screen with cursor
    moves, erase sequences and blank padding. We replay it on a pyte screen and hand out the lines
    the cursor has left behind (they no longer change), plus the current line as `partial`.
    """
    COLS, ROWS = 512, 200

    def __init__(self):
        import pyte
        outer = self

        class _Screen(pyte.Screen):
            def index(self):
                bottom = self.margins.bottom if self.margins else self.lines - 1
                if self.cursor.y == bottom:  # the top line scrolls off
                    if outer.emitted > 0:
                        outer.emitted -= 1
                    else:
                        outer.pending.append(outer.row(0))
                super().index()

        self.pending, self.emitted = [], 0
        self.screen = _Screen(self.COLS, self.ROWS)
        self.stream = pyte.Stream(self.screen)

    def feed(self, data):
        self.stream.feed(data)
        y = self.screen.cursor.y
        if y > self.emitted:
            self.pending.extend(self.row(r) for r in range(self.emitted, y))
            self.emitted = y
        out, self.pending = self.pending, []
        return ''.join(line + '\n' for line in out)

    def row(self, y):
        line = self.screen.buffer[y]  # sparse: only cells that were written
        if not line:
            return ''
        return ''.join(line[x].data for x in range(max(line) + 1)).rstrip()

    def partial(self):
        return self.row(self.screen.cursor.y)


class WinPty(_Base):
    def __init__(self, argv, cwd, env=None):
        try:
            from winpty import PtyProcess
        except ImportError:
            raise RuntimeError('pywinpty and pyte are required on Windows: pip install pywinpty pyte')
        super().__init__()
        self.text = ScreenText()
        self.proc = PtyProcess.spawn(subprocess.list2cmdline(argv), cwd=cwd, env=env,
                                     dimensions=(ScreenText.ROWS, ScreenText.COLS))
        self._start_pump()

    def _read_chunk(self):
        return self.proc.read(65536)

    def _ingest(self, data):
        text = self.text.feed(data)
        if text:
            self._buf.append(text)

    def partial(self):
        with self._cond:
            return self.text.partial()

    def write(self, text):
        # A console Enter is a single CR; CRLF would be read as two Enters (an extra empty command).
        self.proc.write(text.replace('\n', '\r'))

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
