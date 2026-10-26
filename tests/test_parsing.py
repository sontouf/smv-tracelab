from smv_tracelab.parsing import (clean, parse_candidates, parse_check_results, parse_properties,
                                  parse_trace, parse_trace_count, parse_vars)

CANDS_IVAR = """
***************  AVAILABLE STATES  *************

  ================= State =================
  full = FALSE
  cnt = 0
  on = FALSE

    This state is reachable through:
    0) -------------------------
    btn = FALSE


  ================= State =================
  full = FALSE
  cnt = 1
  on = TRUE

    This state is reachable through:
    1) -------------------------
    btn = TRUE


Choose a state from the above (0-1): """

CANDS_PLAIN = """
***************  AVAILABLE STATES  *************

  ================= State =================
  0) -------------------------
  request = TRUE
  state = ready


  ================= State =================
  1) -------------------------
  request = FALSE
  state = ready


Choose a state from the above (0-1): """

TRACE_LOOP = """    <!-- ################### Trace number: 2 ################### -->
Trace Description: LTL Counterexample
Trace Type: Counterexample
  -- Loop starts here
  -> State: 2.1 <-
    cnt = 0
    full = FALSE
  -> Input: 2.2 <-
    btn = FALSE
  -> State: 2.2 <-
    cnt = 0
    full = FALSE
NuSMV > """

PROPS = """**** PROPERTY LIST [ Type, Status, Counter-example Number, Name ] ****
--------------------------  PROPERTY LIST  -------------------------
000 :AG cnt < 3
  [CTL            False          1      c1]
001 :AG (EF cnt = 0)
  [CTL            Unchecked      N/A    N/A]
NuSMV > """


def test_candidates_with_inputs():
    c = parse_candidates(CANDS_IVAR)
    assert [x['index'] for x in c] == [0, 1]
    assert c[1]['input'] == {'btn': 'TRUE'}
    assert c[1]['state'] == {'full': 'FALSE', 'cnt': '1', 'on': 'TRUE'}


def test_candidates_plain():
    c = parse_candidates(CANDS_PLAIN)
    assert c[0] == {'index': 0, 'state': {'request': 'TRUE', 'state': 'ready'}, 'input': {}}
    assert c[1]['state']['request'] == 'FALSE'


def test_trace_with_loop_and_inputs():
    t = parse_trace(TRACE_LOOP)
    assert t['number'] == 2 and t['type'] == 'Counterexample'
    assert [s['label'] for s in t['steps']] == ['2.1', '2.2']
    assert t['loops'] == ['2.1']
    assert t['steps'][1]['input'] == {'btn': 'FALSE'}
    assert t['steps'][0]['input'] == {}


def test_properties():
    p = parse_properties(PROPS)
    assert p[0] == {'index': 0, 'formula': 'AG cnt < 3', 'type': 'CTL', 'status': 'False', 'cex': 1, 'name': 'c1'}
    assert p[1]['cex'] is None and p[1]['name'] is None and p[1]['status'] == 'Unchecked'


def test_misc():
    assert parse_trace_count('There are 5 traces currently available.') == 5
    assert parse_trace_count('There is 1 trace currently available.') == 1
    assert parse_trace_count('There are no traces currently available.') == 0
    assert parse_vars('Number of State Variables: 1\n  cnt : {0, 1}\n') == [{'name': 'cnt', 'type': '{0, 1}'}]
    r = parse_check_results('-- specification AG x  is false\n-- invariant y  is true\n')
    assert r == [{'formula': 'AG x', 'result': 'false'}, {'formula': 'y', 'result': 'true'}]
    assert clean('\x1b[?25lNuSMV > \x1b[0m\r\n') == 'NuSMV > \n'
