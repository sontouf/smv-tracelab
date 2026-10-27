"""One interactive NuSMV process and the state the web UI needs about it."""
import re
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import proc
from .nusmv_bin import LIB_HINT, runtime_env
from .parsing import (clean, parse_candidates, parse_check_results, parse_properties, parse_trace,
                      parse_trace_count, parse_vars)

# Prompts on which NuSMV waits for stdin, matched at the end of the output buffer.
# Trailing [ \t]* tolerates the blank padding ConPTY (Windows) writes after a prompt.
PROMPTS = [
    ('idle', re.compile(r'NuSMV >[ \t]*$')),
    ('choose', re.compile(r'Choose a state from the above \((\d+)-(\d+)\):[ \t]*$')),
    ('single', re.compile(r"There's only one available state\. Press Return to Proceed\.\s*$")),
    ('constraint', re.compile(r'Too many \((\S+)\) (?:future |initial )?states to visualize\. '
                              r'Please specify further constraints:\s*\n>[ \t]*$')),
]
CHECK_CMDS = {'ctl': 'check_ctlspec', 'ltl': 'check_ltlspec', 'invar': 'check_invar'}


class NuSMVError(RuntimeError):
    pass


class Session:
    def __init__(self, nusmv, root):
        self.nusmv = nusmv
        self.env, self.missing_libs = runtime_env(nusmv)
        self.root = Path(root).resolve()
        self.lock = threading.RLock()
        self.p = None
        self.buf = ''
        self.mode = 'none'      # none | idle | choose | single | constraint | busy | dead
        self.prompt_info = {}
        self.candidates = []
        self.model = None
        self.load_ok = False
        self.load_output = ''
        self.vars, self.input_vars = [], []
        self.traces = {}        # number -> parsed trace (cache)
        self.trace_count = 0
        self.sim_trace = None   # trace that simulate extends
        self.view_trace = None  # trace shown in the viewer
        self.pending, self.run = [], None
        self.props, self.last_check = [], None
        self.log = []
        self.tmpdir = Path(tempfile.mkdtemp(prefix='smv_tracelab_'))

    # ------------------------------------------------------------------ process io
    def _log(self, kind, text):
        if text:
            self.log.append({'kind': kind, 'text': text})
            if len(self.log) > 5000:
                del self.log[:1000]

    def _wait(self, timeout, echo=None):
        end = time.time() + timeout
        while True:
            self.buf += clean(self.p.read(0.1))
            if echo is not None and '\n' in self.buf:
                # ConPTY (Windows) echoes the typed line after the old prompt. Until that line is
                # complete, the "current line" is still the old prompt, so do not match prompts yet.
                first, rest = self.buf.split('\n', 1)
                if first.rstrip().endswith(echo):
                    self.buf = rest
                echo = None
            view = self.buf + clean(self.p.partial())
            for name, rx in PROMPTS if echo is None else ():
                m = rx.search(view)
                if m:
                    self.buf = view
                    self.mode = name
                    self.prompt_info = {}
                    if name == 'choose':
                        self.prompt_info = {'min': int(m.group(1)), 'max': int(m.group(2))}
                    elif name == 'constraint':
                        self.prompt_info = {'count': m.group(1)}
                    return self.buf
            if self.p.eof or not self.p.alive():
                self.buf += clean(self.p.read(0.2))
                self.mode = 'dead'
                return self.buf
            if time.time() > end:
                self.mode = 'busy'
                return self.buf

    def _send(self, line, timeout=120, log=True):
        if self.p is None or not self.p.alive():
            raise NuSMVError('NuSMV 프로세스가 실행 중이 아닙니다. 모델을 다시 로드하세요.')
        if self.mode == 'busy':
            self._wait(0.2)
            if self.mode == 'busy':
                raise NuSMVError('NuSMV 가 아직 이전 명령을 처리 중입니다.')
        self.buf = ''
        self.p.write(line + '\n')
        out = self._wait(timeout, echo=line if sys.platform == 'win32' else None)
        if log:
            self._log('cmd', line if line else '⏎ (Return)')
            self._log('out', self._strip_prompt(out))
        return out

    @staticmethod
    def _strip_prompt(text):
        return re.sub(r'NuSMV >[ \t]*$', '', text).rstrip('\n')

    def _kill(self):
        if self.p is not None:
            self.p.kill()
        self.p = None

    # ------------------------------------------------------------------ trace bookkeeping
    def _fetch_trace(self, n):
        tr = parse_trace(self._send('show_traces -v %d' % n, log=False))
        if tr:
            self.traces[n] = tr
        return tr

    def _sync_traces(self, refetch=()):
        """Pull traces created since last sync (plus `refetch`) after NuSMV is back at its prompt."""
        if self.mode != 'idle':
            return
        count = parse_trace_count(self._send('show_traces -t', log=False))
        new = list(range(self.trace_count + 1, count + 1))
        self.trace_count = count
        for n in set(new) | {n for n in refetch if n and n <= count}:
            self._fetch_trace(n)
        return new

    def _after_output(self, out, refetch=()):
        self.candidates = parse_candidates(out) if self.mode in ('choose', 'single') else []
        if 'No future state exists' in out:
            self._log('err', '데드락: 현재 상태(또는 주어진 제약)에서 다음 상태가 존재하지 않습니다.')
        if self.mode == 'idle':
            self.pending, self.run = [], None
            return self._sync_traces(refetch)
        return []

    # ------------------------------------------------------------------ model
    def load(self, path=None, source=None, name=None):
        with self.lock:
            if source is not None:
                name = re.sub(r'[^\w.\-]', '_', name or 'uploaded.smv') or 'uploaded.smv'
                p = self.tmpdir / name
                p.write_text(source, encoding='utf-8')
                self.model = {'path': str(p), 'name': name, 'uploaded': True}
            else:
                p = Path(path).expanduser()
                if not p.is_absolute():
                    p = self.root / p
                p = p.resolve()
                if not p.is_file():
                    raise NuSMVError('파일을 찾을 수 없습니다: %s' % p)
                self.model = {'path': str(p), 'name': p.name, 'uploaded': False}
            return self._boot()

    def restart(self):
        with self.lock:
            if not self.model:
                raise NuSMVError('로드된 모델이 없습니다.')
            return self._boot()

    def _boot(self):
        self._kill()
        path = Path(self.model['path'])
        self.traces, self.trace_count, self.sim_trace, self.view_trace = {}, 0, None, None
        self.candidates, self.pending, self.run, self.vars, self.input_vars = [], [], None, [], []
        self.props, self.last_check = [], None
        self._log('info', '=== %s -int %s ===' % (self.nusmv, path))
        self.p = proc.spawn([self.nusmv, '-int', path.name], cwd=str(path.parent), env=self.env)
        self.buf, self.mode = '', 'busy'
        out = self._wait(30)
        self._log('out', self._strip_prompt(out))
        self.load_ok = False
        if self.mode != 'idle':
            self.load_output = out
            if 'error while loading shared libraries' in out:
                self.load_output += '\n\n' + LIB_HINT % ', '.join(self.missing_libs or ['?'])
            return self.state()
        self._send('set shown_states 100', log=False)
        out = self._send('go', timeout=900)
        self.load_output = self._strip_prompt(out)
        err = re.search(r'(?im)^.*(error|cannot|undefined|not found|syntax|illegal).*$', out)
        self.load_ok = self.mode == 'idle' and not err
        if self.mode == 'idle':
            self.vars = parse_vars(self._send('show_vars -v', log=False))
            self.input_vars = [v['name'] for v in parse_vars(self._send('show_vars -i', log=False))]
            self._refresh_props()
        return self.state()

    # ------------------------------------------------------------------ simulation
    def _require(self, *modes):
        if self.mode not in modes:
            raise NuSMVError('현재 NuSMV 상태(%s)에서는 이 동작을 할 수 없습니다.' % self.mode)

    @staticmethod
    def _quote(expr):
        expr = (expr or '').strip()
        if '"' in expr:
            raise NuSMVError('식에 큰따옴표(")는 사용할 수 없습니다.')
        return expr

    def pick_init(self, constraint='', random=False):
        with self.lock:
            self._require('idle')
            c = self._quote(constraint)
            cmd = 'pick_state ' + ('-r' if random else '-i -a') + (' -c "%s"' % c if c else '')
            self.run, self.pending = {'kind': 'init', 'total': 1, 'done': 0}, []
            out = self._send(cmd)
            self._finish_sim(out, new_trace=True)
            return self.state()

    def simulate(self, steps=1, c='', t='', random=False):
        with self.lock:
            self._require('idle')
            if not self.sim_trace:
                raise NuSMVError('먼저 초기 상태를 선택하세요 (pick_state) 또는 trace 의 한 스텝으로 이동하세요.')
            steps = max(1, min(int(steps), 100000))
            c, t = self._quote(c), self._quote(t)
            cmd = 'simulate %s -k %d' % ('-r' if random else '-i -a', steps)
            cmd += (' -c "%s"' % c if c else '') + (' -t "%s"' % t if t else '')
            self.run, self.pending = {'kind': 'simulate', 'total': steps, 'done': 0}, []
            self.view_trace = self.sim_trace
            out = self._send(cmd, timeout=900)
            self._finish_sim(out)
            return self.state()

    def _finish_sim(self, out, new_trace=False):
        new = self._after_output(out, refetch=() if new_trace else (self.sim_trace,))
        if self.mode == 'idle':
            if new_trace and new:
                self.sim_trace = new[-1]
            self.view_trace = self.sim_trace

    def choose(self, index=None, constraint=None):
        with self.lock:
            chosen = None
            if self.mode == 'choose':
                index = int(index)
                if not (self.prompt_info['min'] <= index <= self.prompt_info['max']):
                    raise NuSMVError('선택 범위를 벗어났습니다.')
                chosen = next((c for c in self.candidates if c['index'] == index), None)
                out = self._send(str(index))
            elif self.mode == 'single':
                chosen = self.candidates[0] if self.candidates else None
                out = self._send('')
            elif self.mode == 'constraint':
                out = self._send(self._quote(constraint) or 'TRUE')
            else:
                raise NuSMVError('지금은 선택 대기 상태가 아닙니다.')
            if chosen is not None:
                self.pending.append(chosen)
                if self.run:
                    self.run['done'] += 1
            pending, run = self.pending, self.run
            kind = run['kind'] if run else None
            self._finish_sim(out, new_trace=(kind == 'init'))
            if self.mode != 'idle':
                self.pending, self.run = pending, run
            return self.state()

    def goto(self, label):
        """goto_state N.k: copies trace N up to step k into a new trace and continues from there."""
        with self.lock:
            self._require('idle')
            if not re.fullmatch(r'\d+\.\d+', label or ''):
                raise NuSMVError('상태 라벨 형식은 N.k 입니다 (예: 1.3).')
            out = self._send('goto_state %s' % label)
            new = self._after_output(out)
            if new:
                self.sim_trace = self.view_trace = new[-1]
            return self.state()

    def view(self, number):
        with self.lock:
            number = int(number)
            if number not in self.traces:
                self._require('idle')
                if not self._fetch_trace(number):
                    raise NuSMVError('trace %d 이 없습니다.' % number)
            self.view_trace = number
            return self.state()

    # ------------------------------------------------------------------ verifier
    def _refresh_props(self):
        self.props = parse_properties(self._send('show_property', log=False))

    def check(self, index=None):
        """check_property -n index, or every property when index is None."""
        with self.lock:
            self._require('idle')
            cmd = 'check_property' + ('' if index is None else ' -n %d' % int(index))
            return self._run_check(cmd, index)

    def check_formula(self, kind, formula):
        with self.lock:
            self._require('idle')
            if kind not in CHECK_CMDS:
                raise NuSMVError('알 수 없는 property 종류: %s' % kind)
            f = self._quote(formula)
            if not f:
                raise NuSMVError('식을 입력하세요.')
            return self._run_check('%s -p "%s"' % (CHECK_CMDS[kind], f), None)

    def _run_check(self, cmd, index):
        out = self._send(cmd, timeout=3600)
        new = self._after_output(out)
        self._refresh_props()
        results = parse_check_results(out)
        error = None
        if not results:
            error = self._strip_prompt(out).strip()[-1500:] or '결과를 해석하지 못했습니다.'
        self.last_check = {'cmd': cmd, 'results': results, 'new_traces': new, 'error': error}
        if index is not None:
            prop = next((p for p in self.props if p['index'] == int(index)), None)
            if prop and prop['cex']:
                self.view_trace = prop['cex']
        elif new:
            self.view_trace = new[-1]
        return self.state()

    # ------------------------------------------------------------------ misc
    def raw(self, line):
        with self.lock:
            if self.mode in ('none', 'dead'):
                raise NuSMVError('NuSMV 프로세스가 실행 중이 아닙니다.')
            out = self._send(line, timeout=900)
            new = self._after_output(out, refetch=(self.sim_trace,))
            if self.mode == 'idle':
                if new:
                    self.view_trace = new[-1]
                if re.match(r'\s*(check_|read_model|go|reset)', line):
                    self._refresh_props()
            return self.state()

    def poll(self):
        with self.lock:
            if self.mode == 'busy' and self.p:
                out = self._wait(0.1)
                if self.mode != 'busy':
                    self._log('out', self._strip_prompt(out))
                    self._after_output(out, refetch=(self.sim_trace,))
            return self.state()

    def state(self):
        tr = self.traces.get(self.view_trace)
        return {
            'nusmv': self.nusmv, 'root': str(self.root), 'mode': self.mode, 'prompt': self.prompt_info,
            'model': self.model, 'load_ok': self.load_ok, 'load_output': self.load_output,
            'vars': self.vars, 'input_vars': self.input_vars,
            'candidates': self.candidates if self.mode in ('choose', 'single') else [],
            'pending': self.pending, 'run': self.run,
            'trace': tr, 'sim_trace': self.sim_trace,
            'traces': [{'number': n, 'description': t['description'], 'type': t['type'], 'length': len(t['steps'])}
                       for n, t in sorted(self.traces.items())],
            'props': self.props, 'last_check': self.last_check,
            'log_len': len(self.log),
        }

    def list_models(self):
        res = []
        for p in sorted(self.root.rglob('*.smv')):
            rel = p.relative_to(self.root)
            if any(part.startswith('.') or part in ('node_modules', '__pycache__') for part in rel.parts):
                continue
            res.append(rel.as_posix())
            if len(res) >= 500:
                break
        return res

    def close(self):
        self._kill()
        shutil.rmtree(self.tmpdir, ignore_errors=True)
