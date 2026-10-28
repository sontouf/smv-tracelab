"""Grid-map world for closed-loop simulation of a robot vacuum model.

The world is a W x H grid; cells outside it count as walls. Each NuSMV step the
robot's sensors (front/left/right blocked, dust under the robot) are computed
from its pose and fed to `simulate` as a constraint; the motor and cleaner
values of the reached state then move the robot and clean the map.
"""

W = H = 10
# Heading -> (dx, dy). y grows upwards, (0, 0) is the bottom-left cell.
DIRS = {'N': (0, 1), 'E': (1, 0), 'S': (0, -1), 'W': (-1, 0)}
LEFT = {'N': 'W', 'W': 'S', 'S': 'E', 'E': 'N'}
RIGHT = {v: k for k, v in LEFT.items()}

DEFAULT_MAPPING = {
    'tick': 'tick',
    'front': 'sensor.front', 'left': 'sensor.left', 'right': 'sensor.right', 'dust': 'sensor.dust',
    'motor': 'motor.motor', 'clean': 'cleaner.clean_mode',
    'forward': 'moveforward', 'backward': 'movebackward', 'turn_left': 'turnleft', 'turn_right': 'turnright',
    'cleaning': 'clean_on,clean_up',  # cleaner values that count the robot's cell as cleaned
    'removing': 'clean_up',           # cleaner values that remove the dust under the robot
}
SENSORS = ('front', 'left', 'right', 'dust')


class GridError(ValueError):
    pass


def _cells(items, what):
    out = []
    for c in items or []:
        try:
            x, y = int(c[0]), int(c[1])
        except (TypeError, ValueError, IndexError):
            raise GridError('%s 좌표 형식이 잘못되었습니다: %r' % (what, c))
        if not (0 <= x < W and 0 <= y < H):
            raise GridError('%s 좌표가 맵 밖입니다: (%d,%d)' % (what, x, y))
        out.append((x, y))
    return out


def norm_mapping(mapping):
    m = dict(DEFAULT_MAPPING)
    m.update({k: str(v).strip() for k, v in (mapping or {}).items() if k in DEFAULT_MAPPING and v is not None})
    return m


def _values(s):
    return {v.strip() for v in s.split(',') if v.strip()}


class World:
    def __init__(self, obstacles, dust, start=(0, 0), heading='E', mapping=None):
        self.obstacles = set(_cells(obstacles, '장애물'))
        self.dust = set(_cells(dust, '먼지'))
        self.start = tuple(_cells([start], '시작')[0])
        if heading not in DIRS:
            raise GridError('방향은 N/E/S/W 중 하나입니다.')
        if self.start in self.obstacles:
            raise GridError('시작 위치에 장애물이 있습니다.')
        if self.dust & self.obstacles:
            raise GridError('먼지와 장애물이 겹칩니다.')
        self.m = norm_mapping(mapping)
        self.cleaning, self.removing = _values(self.m['cleaning']), _values(self.m['removing'])
        self.pos, self.heading = self.start, heading
        self.dust_left = set(self.dust)
        self.cleaned = set()
        self.turning = None  # motor value of the turn in progress (rotate once per turn)

    def blocked(self, x, y):
        return not (0 <= x < W and 0 <= y < H) or (x, y) in self.obstacles

    def _ahead(self, heading, sign=1):
        dx, dy = DIRS[heading]
        return self.pos[0] + sign * dx, self.pos[1] + sign * dy

    def sense(self):
        return {'front': self.blocked(*self._ahead(self.heading)),
                'left': self.blocked(*self._ahead(LEFT[self.heading])),
                'right': self.blocked(*self._ahead(RIGHT[self.heading])),
                'dust': self.pos in self.dust_left}

    def constraint(self, tick=None, skip=()):
        """NuSMV simple expression fixing the clock and the mapped sensor variables (except `skip`)."""
        sense = self.sense()
        parts = []
        if tick is not None and self.m['tick']:
            parts.append('%s = %s' % (self.m['tick'], 'TRUE' if tick else 'FALSE'))
        parts += ['%s = %s' % (self.m[k], 'TRUE' if sense[k] else 'FALSE') for k in SENSORS if self.m[k] and self.m[k] not in skip]
        return ' & '.join(parts)

    def acts(self, state):
        """Whether the transition out of `state` moves the robot: once per clock cycle, out of tick = TRUE."""
        t = self.m['tick']
        return not t or state.get(t) != 'FALSE'

    def act(self, state):
        """Apply the actuators of `state` (one transition). Returns a short event dict."""
        ev = {}
        if not self.acts(state):
            return ev
        motor, clean = state.get(self.m['motor']), state.get(self.m['clean'])
        if clean in self.cleaning:
            self.cleaned.add(self.pos)
            ev['cleaned'] = list(self.pos)
        if clean in self.removing and self.pos in self.dust_left:
            self.dust_left.discard(self.pos)
            ev['removed'] = list(self.pos)
        if motor in (self.m['turn_left'], self.m['turn_right']):
            if self.turning != motor:
                self.heading = (LEFT if motor == self.m['turn_left'] else RIGHT)[self.heading]
                ev['turn'] = 'L' if motor == self.m['turn_left'] else 'R'
            self.turning = motor
            return ev
        self.turning = None
        if motor in (self.m['forward'], self.m['backward']):
            nxt = self._ahead(self.heading, 1 if motor == self.m['forward'] else -1)
            if self.blocked(*nxt):
                ev['bump'] = list(nxt)
            else:
                self.pos = nxt
                ev['move'] = 'F' if motor == self.m['forward'] else 'B'
        return ev

    def snapshot(self):
        return {'x': self.pos[0], 'y': self.pos[1], 'h': self.heading,
                'dust_left': len(self.dust_left), 'cleaned': len(self.cleaned)}


def replay(world_args, steps):
    """Rebuild robot frames from a parsed trace's steps (the trace NuSMV stored for the run)."""
    w = World(**world_args)
    frames = []
    for i, st in enumerate(steps):
        vals = dict(st['input'], **st['state'])
        sense = w.sense()
        mism = [k for k in SENSORS if w.m[k] in vals and vals[w.m[k]] != ('TRUE' if sense[k] else 'FALSE')]
        f = w.snapshot()
        f.update({'label': st['label'], 'sense': sense, 'mismatch': mism, 'vars': vals,
                  'tick': vals.get(w.m['tick']), 'motor': vals.get(w.m['motor']), 'clean': vals.get(w.m['clean'])})
        f['event'] = w.act(vals) if i + 1 < len(steps) else {}
        frames.append(f)
    return frames
