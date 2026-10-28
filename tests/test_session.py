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
    assert st['load_ok'], st['load_output']
    assert st['input_vars'] == ['btn']
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


def test_multi_component_inputs_and_stop(s):
    st = s.load(path='robot_components.smv')
    assert st['load_ok'], st['load_output']
    assert st['input_vars'] == ['sensor.obstacle', 'sensor.halt']
    st = s.pick_init()
    assert st['mode'] == 'single'
    st = s.choose()
    # Idle -> Moving -> (obstacle) Stop -> Turning: inputs live in the sensor sub-module
    st = s.simulate(3)
    plan = [('FALSE', 'FALSE'), ('TRUE', 'FALSE'), ('FALSE', 'FALSE')]
    for obstacle, halt in plan:
        assert st['mode'] in ('choose', 'single')
        if st['mode'] == 'single':
            st = s.choose()
            continue
        pick = next(c for c in st['candidates']
                    if c['input'] == {'sensor.obstacle': obstacle, 'sensor.halt': halt})
        st = s.choose(pick['index'])
    assert st['mode'] == 'idle'
    steps = st['trace']['steps']
    assert [x['state']['ctrl.state'] for x in steps] == ['Idle', 'Moving', 'Stop', 'Turning']
    assert [x['state']['motor.motor'] for x in steps] == ['off', 'forward', 'braking', 'turning']
    assert steps[2]['input'] == {'sensor.obstacle': 'TRUE', 'sensor.halt': 'FALSE'}
    st = s.check()
    assert {p['name']: p['status'] for p in st['props']} == {
        'stop_before_turn': 'True', 'brake_in_stop': 'True', 'resumes': 'False', 'no_idle_turn': 'True'}


def test_clocked_components_full_domains_and_counterexample(s):
    st = s.load(path='clocked_components.smv')
    assert st['load_ok'], st['load_output']
    # show_vars -v lists every enum value (plain show_vars abbreviates long domains)
    types = {v['name']: v['type'] for v in st['vars']}
    assert types['ctrl.state'] == '{Cruise, Stop, Reverse, TurnLeft, TurnRight, Vacuum, Dock}'
    st = s.pick_init()
    st = s.choose() if st['mode'] == 'single' else s.choose(st['candidates'][0]['index'])
    st = s.simulate(4, random=True)
    steps = st['trace']['steps']
    assert [x['state']['positive_edge'] for x in steps] == ['TRUE', 'FALSE', 'TRUE', 'FALSE', 'TRUE']
    # components change only on transitions that follow a positive edge
    for a, b in zip(steps, steps[1:]):
        if a['state']['positive_edge'] == 'FALSE':
            assert a['state']['ctrl.state'] == b['state']['ctrl.state']
    st = s.check()
    assert {p['name']: p['status'] for p in st['props']} == {
        'hold_between_edges': 'True', 'obstacle_stops': 'True', 'leaves_vacuum': 'False', 'brush_off_in_stop': 'True'}
    cex = next(p['cex'] for p in st['props'] if p['name'] == 'leaves_vacuum')
    st = s.view(cex)
    assert st['trace']['type'] == 'Counterexample' and st['trace']['loops']


def test_grid_closed_loop(s):
    import time
    st = s.load(path='grid_vacuum.smv')
    assert st['load_ok'], st['load_output']
    s.grid_start({'obstacles': [[3, 0], [2, 2]], 'dust': [[1, 0], [0, 5]], 'heading': 'E', 'steps': 60})
    end = time.time() + 60
    while s.grid_running and time.time() < end:
        time.sleep(0.05)
    g = s.grid_result()
    assert g['status'] == 'done', g['error']
    fr = g['frames']
    assert len(fr) == 61 and g['trace'] == s.sim_trace
    assert [f['tick'] for f in fr[:4]] == ['FALSE', 'TRUE', 'FALSE', 'TRUE']
    assert not any(f['mismatch'] for f in fr)
    assert fr[-1]['dust_left'] < 2                        # (1,0) is right in front of the start
    assert any(f['event'].get('removed') == [1, 0] for f in fr)
