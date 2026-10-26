"""Parsers for NuSMV interactive-shell output."""
import re

VAR_LINE = re.compile(r'^\s*(\S+) = (.*?)\s*$')
IDX_LINE = re.compile(r'^\s*(\d+)\) -+\s*$')
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[=>()][0-9A-Za-z]?')


def clean(text):
    """Normalise newlines and strip terminal escape sequences (ConPTY emits them on Windows)."""
    text = ANSI.sub('', text)
    return text.replace('\r\n', '\n').replace('\r', '\n')


def parse_candidates(text):
    """'AVAILABLE STATES' block of `pick_state -i -a` / `simulate -i -a` -> [{index, state, input}]."""
    pos = text.rfind('AVAILABLE STATES')
    if pos < 0:
        return []
    out = []
    for block in text[pos:].split('================= State =================')[1:]:
        state, inputs, state_idx, in_input = {}, [], None, False
        for line in block.split('\n'):
            if 'reachable through' in line:
                in_input = True
                continue
            if line.startswith('Choose a state') or line.startswith("There's only one"):
                break
            m = IDX_LINE.match(line)
            if m:
                if in_input:
                    inputs.append((int(m.group(1)), {}))
                else:
                    state_idx = int(m.group(1))
                continue
            m = VAR_LINE.match(line)
            if m:
                (inputs[-1][1] if in_input and inputs else state)[m.group(1)] = m.group(2)
        if inputs:
            out.extend({'index': i, 'state': state, 'input': inp} for i, inp in inputs)
        elif state_idx is not None:
            out.append({'index': state_idx, 'state': state, 'input': {}})
    out.sort(key=lambda c: c['index'])
    return out


def parse_trace(text):
    """`show_traces -v N` -> {number, description, type, steps:[{label, step, input, state}], loops}.

    With -v every state lists all variables, so no diff reconstruction is needed.
    `loops` holds the labels of states marked by '-- Loop starts here'.
    """
    m = re.search(r'Trace number: (\d+)', text)
    if not m:
        return None
    d = re.search(r'Trace Description: (.*)', text)
    t = re.search(r'Trace Type: (.*)', text)
    tr = {'number': int(m.group(1)), 'description': d.group(1).strip() if d else '',
          'type': t.group(1).strip() if t else '', 'steps': [], 'loops': []}
    target, pending_input, loop_next = None, {}, False
    for line in text.split('\n'):
        ms = re.match(r'\s*-> State: (\d+)\.(\d+) <-', line)
        if ms:
            cur = {'label': '%s.%s' % (ms.group(1), ms.group(2)), 'step': int(ms.group(2)),
                   'input': pending_input, 'state': {}}
            pending_input = {}
            if loop_next:
                tr['loops'].append(cur['label'])
                loop_next = False
            tr['steps'].append(cur)
            target = cur['state']
            continue
        if re.match(r'\s*-> Input: (\d+)\.(\d+) <-', line):
            pending_input = {}
            target = pending_input
            continue
        if 'Loop starts here' in line:
            loop_next = True
            continue
        mv = VAR_LINE.match(line)
        if mv and target is not None:
            target[mv.group(1)] = mv.group(2)
    return tr


def parse_vars(text):
    """`show_vars` -> [{name, type}] (the '  name : type' lines)."""
    res = []
    for line in text.split('\n'):
        m = re.match(r'^  (\S+) : (.+?)\s*$', line)
        if m:
            res.append({'name': m.group(1), 'type': m.group(2)})
    return res


def parse_trace_count(text):
    """`show_traces -t` -> number of stored traces."""
    m = re.search(r'There (?:are|is) (\d+) traces?', text)
    return int(m.group(1)) if m else 0


def parse_properties(text):
    """`show_property` -> [{index, formula, type, status, cex, name}]."""
    props, cur = [], None
    for line in text.split('\n'):
        m = re.match(r'^(\d{3,}) :(.*)$', line)
        if m:
            cur = {'index': int(m.group(1)), 'formula': m.group(2).strip(), 'type': '', 'status': '',
                   'cex': None, 'name': None}
            props.append(cur)
            continue
        m = re.match(r'^\s+\[(\S+)\s+(\S+)\s+(\S+)\s+(.*?)\s*\]\s*$', line)
        if m and cur is not None:
            cur['type'], cur['status'] = m.group(1), m.group(2)
            cur['cex'] = int(m.group(3)) if m.group(3).isdigit() else None
            cur['name'] = None if m.group(4) == 'N/A' else m.group(4)
            cur = None
            continue
        if cur is not None and line.strip() and not line.startswith('NuSMV'):
            cur['formula'] += ' ' + line.strip()
    return props


def parse_check_results(text):
    """Verdict lines of check_* commands -> [{text, result}] where result is 'true' or 'false'."""
    res = []
    for m in re.finditer(r'^-- (?:specification|invariant) (.*?)\s+is (true|false)\s*$', text, re.M):
        res.append({'formula': m.group(1).strip(), 'result': m.group(2)})
    return res
