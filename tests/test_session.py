"""End-to-end tests against a real NuSMV process (skipped when NuSMV is not available)."""
from pathlib import Path

import pytest

from smv_tracelab.nusmv_bin import find_nusmv
from smv_tracelab.session import Session

EXAMPLES = Path(__file__).resolve().parent.parent / 'examples'
NUSMV = find_nusmv()
pytestmark = pytest.mark.skipif(not NUSMV, reason='NuSMV not found (run: smv-tracelab --install-nusmv)')


@pytest.fixture
def s():
    sess = Session(NUSMV, EXAMPLES)
    yield sess
    sess.close()


def test_interactive_simulation(s):
    st = s.load(path='counter.smv')
    assert st['load_ok'] and st['input_vars'] == ['btn']
    st = s.pick_init()
    assert st['mode'] == 'choose' and len(st['candidates']) == 2
    st = s.choose(st['candidates'][1]['index'])
    assert st['mode'] == 'idle' and st['sim_trace'] == 1
    st = s.simulate(3)
    for _ in range(3):
        assert st['mode'] == 'choose'
        pick = next(c for c in st['candidates'] if c['input'].get('btn') == 'TRUE')
        st = s.choose(pick['index'])
    assert st['mode'] == 'idle'
    assert [x['state']['cnt'] for x in st['trace']['steps']] == ['0', '1', '2', '3']
    assert st['trace']['steps'][1]['input'] == {'btn': 'TRUE'}


def test_branch_with_goto(s):
    s.load(path='counter.smv')
    s.pick_init(random=True)
    s.simulate(4, random=True)
    st = s.goto('1.3')
    assert st['sim_trace'] == 2 and len(st['trace']['steps']) == 3


def test_single_candidate_and_constraint_prompt(s):
    st = s.load(path='request_busy.smv')
    st = s.pick_init(constraint='request')
    assert st['mode'] == 'single'
    st = s.choose()
    assert st['mode'] == 'idle'


def test_verifier_counterexamples(s):
    st = s.load(path='counter.smv')
    assert [p['name'] for p in st['props']] == ['never_full', 'can_reset', 'always_full', 'on_follows', 'in_range']
    st = s.check(0)
    assert st['last_check']['results'][0]['result'] == 'false'
    assert st['trace']['type'] == 'Counterexample' and st['trace']['steps'][-1]['state']['cnt'] == '3'
    st = s.check(2)
    assert st['trace']['loops']
    st = s.check()
    assert {p['name']: p['status'] for p in st['props']} == {
        'never_full': 'False', 'can_reset': 'True', 'always_full': 'False', 'on_follows': 'True', 'in_range': 'True'}
    st = s.check_formula('ltl', 'F full')
    assert st['last_check']['results'][0]['result'] == 'false' and st['last_check']['new_traces']
    st = s.check_formula('ctl', 'AG (')
    assert st['last_check']['error']
