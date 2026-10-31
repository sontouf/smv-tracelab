import pytest

from smv_tracelab.grid import GridError, World, replay


def st(motor, clean='clean_off', tick='TRUE'):
    return {'tick': tick, 'motor.motor': motor, 'cleaner.clean_mode': clean}


def test_sensing_walls_and_obstacles():
    w = World(obstacles=[(0, 1)], dust=[(0, 0)], heading='E')
    # facing E at (0,0): left (N) is the obstacle, right (S) is the outer wall
    assert w.sense() == {'front': False, 'left': True, 'right': True, 'dust': True}
    assert w.constraint(False) == ('tick = FALSE & sensor.front = FALSE & sensor.left = TRUE & '
                                   'sensor.right = TRUE & sensor.dust = TRUE')
    assert w.constraint(None, skip={'sensor.dust'}).endswith('sensor.right = TRUE')


def test_motion_once_per_clock_cycle():
    w = World(obstacles=[(2, 0)], dust=[(1, 0)], heading='E')
    assert w.act(st('moveforward', tick='FALSE')) == {}          # positive edge: no motion
    assert w.act(st('moveforward', 'clean_on')) == {'cleaned': [0, 0], 'move': 'F'}
    assert w.pos == (1, 0) and w.sense()['dust']
    assert w.act(st('moveforward', 'clean_up')) == {'cleaned': [1, 0], 'removed': [1, 0], 'bump': [2, 0]}
    assert w.pos == (1, 0) and not w.dust_left


def test_turn_rotates_once_per_turn():
    w = World(obstacles=[], dust=[], heading='E')
    assert w.act(st('turnleft'))['turn'] == 'L' and w.heading == 'N'
    assert w.act(st('turnleft')) == {} and w.heading == 'N'      # still the same turn
    w.act(st('motorstop'))
    w.act(st('turnright'))
    assert w.heading == 'E'
    w.act(st('movebackward'))
    assert w.pos == (0, 0)                                       # wall behind


def test_backward_marks_cell_cleaned():
    w = World(obstacles=[], dust=[], start=(1, 0), heading='E')
    assert w.act(st('movebackward')) == {'cleaned': [1, 0], 'move': 'B'}
    assert w.pos == (0, 0) and w.cleaned == {(1, 0)}
    assert w.act(st('movebackward')) == {'cleaned': [0, 0], 'bump': [-1, 0]}


def test_dust_removed_when_removal_mode_follows_the_edge():
    # dust sensed at (1,0) on the edge while the held moveforward already drives off it
    w = World(obstacles=[], dust=[(1, 0), (3, 0)], start=(1, 0), heading='E')
    assert w.act(st('moveforward', 'clean_on')) == {'cleaned': [1, 0], 'move': 'F'}
    assert w.pos == (2, 0) and (1, 0) in w.dust_left
    assert w.act(st('moveforward', 'clean_up', 'FALSE')) == {'removed': [1, 0], 'cleaned': [1, 0]}
    assert w.dust_left == {(3, 0)}


def test_dust_stays_when_no_removal_mode_follows():
    w = World(obstacles=[], dust=[(1, 0)], start=(1, 0), heading='E')
    w.act(st('moveforward', 'clean_on'))
    assert w.act(st('motorstop', 'clean_off', 'FALSE')) == {}
    assert w.dust_left == {(1, 0)}


def test_removal_mode_before_any_edge_removes_nothing():
    w = World(obstacles=[], dust=[(0, 0)], heading='E')
    assert w.act(st('motorstop', 'clean_up', 'FALSE')) == {}
    assert w.dust_left == {(0, 0)}


def test_custom_start():
    w = World(obstacles=[(0, 0)], dust=[(9, 9)], start=[9, 9], heading='N')
    assert w.pos == (9, 9)
    assert w.sense() == {'front': True, 'left': False, 'right': True, 'dust': True}
    fr = replay({'obstacles': [], 'dust': [], 'start': [5, 4], 'heading': 'E'},
                [{'label': '1.1', 'input': {}, 'state': st('moveforward')},
                 {'label': '1.2', 'input': {}, 'state': st('motorstop')}])
    assert [(f['x'], f['y']) for f in fr] == [(5, 4), (6, 4)]


@pytest.mark.parametrize('start', [(10, 0), (0, 10), (-1, 0)])
def test_start_outside_map(start):
    with pytest.raises(GridError):
        World(obstacles=[], dust=[], start=start)


def test_invalid_maps():
    with pytest.raises(GridError):
        World(obstacles=[(0, 0)], dust=[])
    with pytest.raises(GridError):
        World(obstacles=[(4, 4)], dust=[], start=(4, 4))
    with pytest.raises(GridError):
        World(obstacles=[(3, 3)], dust=[(3, 3)])
    with pytest.raises(GridError):
        World(obstacles=[(10, 0)], dust=[])


def test_replay_frames():
    steps = [{'label': '1.%d' % (i + 1), 'input': {}, 'state': s} for i, s in enumerate(
        [st('motorstop', tick='FALSE'), st('moveforward', 'clean_on'), st('moveforward', 'clean_on', 'FALSE')])]
    for s in steps:
        s['state']['sensor.front'] = 'FALSE'
    fr = replay({'obstacles': [], 'dust': [], 'heading': 'E'}, steps)
    assert [(f['x'], f['y']) for f in fr] == [(0, 0), (0, 0), (1, 0)]
    assert fr[1]['event'] == {'cleaned': [0, 0], 'move': 'F'} and fr[2]['cleaned'] == 1
    assert not any(f['mismatch'] for f in fr)
