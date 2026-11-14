"""Cockpit view for the flight game: ships, radar and gauges on the round
240x240 screen (origin at the centre, y down).

Measured on the badge, a filled rectangle costs about 0.03 ms, a line about
0.35 ms and a piece of text about 4 ms. So everything that is drawn every
frame is built from rectangles where it can be, including the numbers, and
text is kept for messages that only show now and then.
"""
import math

FOCAL = 208.0    # pixels; gives a field of view of about 60 degrees
NEAR = 30.0      # nothing closer than this is drawn
TAU = 2 * math.pi
WIRE_MIN = 8.0   # ships that look smaller than this (pixels) are drawn as a marker
STREAK_MIN = 300  # world units: the shortest stardust streak with the jump drive on

RADAR_Y = 88
RADAR_RX = 58
RADAR_RY = 24
RADAR_RANGE = 10000.0
_RADAR = tuple(
    (RADAR_RX * math.cos(i * TAU / 24) - 0.75, RADAR_Y + RADAR_RY * math.sin(i * TAU / 24) - 0.75)
    for i in range(24)
)

_RADAR_R = int(RADAR_RANGE)
_RADAR_R2 = _RADAR_R * _RADAR_R

WHITE = (1, 1, 1)
DIM = (0.2, 0.2, 0.26)
GREEN = (0.3, 1, 0.4)
YELLOW = (1, 0.85, 0.2)
RED = (1, 0.25, 0.2)
ORANGE = (1, 0.55, 0.1)
CYAN = (0.3, 0.9, 1)
MAGENTA = (1, 0.3, 1)
GREY = (0.7, 0.7, 0.75)
COLOUR = {"ship": WHITE, "rock": GREY, "can": YELLOW, "missile": MAGENTA, "pmissile": CYAN,
          "station": WHITE}
PLANET = (0.35, 0.6, 1)
SUN = (1, 0.8, 0.25)

# seven-segment digits: which of the segments a-g are lit for 0-9
_SEGS = ("abcdef", "bc", "abged", "abgcd", "fgbc", "afgcd", "afgedc", "abc", "abcdefg", "abcdfg")


def _digit_rects(w, h, t):
    """For each digit, the rectangles (x, y, w, h) that draw it in a w by h cell."""
    half = (h - t) / 2.0
    seg = {
        "a": (0, 0, w, t),
        "b": (w - t, 0, t, half + t),
        "c": (w - t, half, t, half + t),
        "d": (0, h - t, w, t),
        "e": (0, half, t, half + t),
        "f": (0, 0, t, half + t),
        "g": (0, half, w, t),
    }
    return tuple(tuple(seg[c] for c in segs) for segs in _SEGS)


_BIG = (7, 11, 2, _digit_rects(7, 11, 2))      # (width, height, thickness, rectangles)
_SMALL = (4, 7, 1, _digit_rects(4, 7, 1))


def number(ctx, value, x, y, font=_BIG, align=0):
    """Draw a whole number out of rectangles in the current colour. (x, y) is
    the top of the number: its centre for align 0, its left edge for align -1,
    its right edge for align 1. Returns the width drawn."""
    w, h, t, rects = font
    s = str(int(value))
    step = w + t + 1
    width = len(s) * step - (t + 1)
    if align == 0:
        x -= width / 2.0
    elif align > 0:
        x -= width
    for ch in s:
        if ch != "-":
            for rx, ry, rw, rh in rects[ord(ch) - 48]:
                ctx.rectangle(x + rx, y + ry, rw, rh).fill()
        x += step
    return width


def text(ctx, s, x, y, size, rgb):
    ctx.font_size = size
    ctx.text_align = ctx.CENTER
    ctx.text_baseline = ctx.MIDDLE
    ctx.rgb(*rgb).move_to(x, y).text(s)


# On the badge every float result is a new 16-byte object on the heap, and a
# frame that makes many of them soon has to stop for a garbage collection.
# Small whole numbers cost nothing, so the loops below work in whole numbers:
# positions in world units, directions times 4096, and screen positions in
# whole pixels.
IFOCAL = int(FOCAL)
INEAR = int(NEAR)
_PX = [0] * 48      # screen x of each vertex of the ship being drawn
_PY = [0] * 48      # screen y
_OK = [False] * 48  # False if the vertex is behind the viewer
_VIS = [False] * 24  # which faces of the ship being drawn can be seen


def draw_ship(ctx, o, rgb=None):
    """Draw one object, in its kind's colour or rgb. Returns True if any of it
    was on screen."""
    z = int(o.z)
    if z < INEAR:
        return False
    bp = o.bp
    x = int(o.x)
    y = int(o.y)
    sx = x * IFOCAL // z
    sy = -(y * IFOCAL // z)
    pr = bp.isize * IFOCAL // z     # how big it looks, in pixels
    if sx - pr > 120 or sx + pr < -120 or sy - pr > 120 or sy + pr < -120:
        return False
    ctx.rgb(*(RED if o.hit > 0 else rgb or COLOUR[o.kind]))
    if pr < 3:          # too far away to make out: a dot
        ctx.rectangle(sx - 1, sy - 1, 2, 2).fill()
        return True
    if pr < WIRE_MIN:   # middle distance: a small marker, much cheaper than lines
        ctx.rectangle(sx - pr, sy - 1, 2 * pr, 1.5).fill()
        h = pr // 2
        ctx.rectangle(sx - 1, sy - h, 1.5, 2 * h).fill()
        return True

    m = o.m
    m0 = int(m[0] * 4096)
    m1 = int(m[1] * 4096)
    m2 = int(m[2] * 4096)
    m3 = int(m[3] * 4096)
    m4 = int(m[4] * 4096)
    m5 = int(m[5] * 4096)
    m6 = int(m[6] * 4096)
    m7 = int(m[7] * 4096)
    m8 = int(m[8] * 4096)
    px = _PX
    py = _PY
    ok = _OK
    i = 0
    for vx, vy, vz in bp.verts:
        pz = z + ((vx * m2 + vy * m5 + vz * m8) >> 12)
        if pz < INEAR:
            ok[i] = False   # behind the viewer
        else:
            ok[i] = True
            px[i] = (x + ((vx * m0 + vy * m3 + vz * m6) >> 12)) * IFOCAL // pz
            py[i] = -((y + ((vx * m1 + vy * m4 + vz * m7) >> 12)) * IFOCAL // pz)
        i += 1

    # Elite's hidden-line rule: a face shows if its normal points back along
    # the line from the viewer to the ship. A zero normal always shows.
    vis = _VIS
    i = 0
    for nx, ny, nz in bp.normals:
        vis[i] = ((nx == 0 and ny == 0 and nz == 0)
                  or ((nx * m0 + ny * m3 + nz * m6) >> 8) * x
                  + ((nx * m1 + ny * m4 + nz * m7) >> 8) * y
                  + ((nx * m2 + ny * m5 + nz * m8) >> 8) * z < 0)
        i += 1

    ctx.begin_path()
    for a, b, f1, f2 in bp.edges:
        if (vis[f1] or vis[f2]) and ok[a] and ok[b]:
            ctx.move_to(px[a], py[a])
            ctx.line_to(px[b], py[b])
    ctx.stroke()
    return True


def _enemy_beam(ctx, o):
    """An enemy laser: from the ship's gun to your cockpit, which is just
    below the view, so the beam runs off the bottom of the screen."""
    bp = o.bp
    m = o.m
    vx, vy, vz = bp.verts[bp.gun] if bp.gun < len(bp.verts) else (0, 0, 0)
    pz = o.z + vx * m[2] + vy * m[5] + vz * m[8]
    if pz < NEAR:
        return
    k = FOCAL / pz
    gx = (o.x + vx * m[0] + vy * m[3] + vz * m[6]) * k
    gy = -(o.y + vx * m[1] + vy * m[4] + vz * m[7]) * k
    if gy > 120:
        return
    ctx.line_width = 2.5
    ctx.rgb(*ORANGE)
    ctx.begin_path()
    ctx.move_to(gx, gy)
    ctx.line_to(gx * 0.25, 126)
    ctx.stroke()
    ctx.line_width = 1.5


# the hyperspace streaks: (direction x, y times 256, starting radius in pixels)
_WARP_T = 1.2       # the same as game.WARP_TIME
_STREAKS = tuple(
    (int(256 * math.cos(i * TAU / 18 + 0.2)), int(256 * math.sin(i * TAU / 18 + 0.2)),
     8 + (i * 29) % 34)
    for i in range(18))


# A station's colour says what kind of government runs the system, warm for
# the dangerous ones and cool for the safe ones, in the order of
# galaxy.GOVERNMENTS. It is used for the tunnel's mouth and the badge's LEDs as
# you launch. No red (the LEDs flash red when you are hit) and no white (the
# tunnel is white).
STATION_COLOURS = (
    ORANGE,             # Anarchy
    YELLOW,             # Feudal
    MAGENTA,            # Multi-government
    (1, 0.5, 0.75),     # Dictatorship: pink
    (0.65, 0.4, 1),     # Communist: violet
    (0.4, 0.55, 1),     # Confederacy: blue
    CYAN,               # Democracy
    GREEN,              # Corporate State
)


def station_colour(government):
    """The colour of a station in a system with this government (0 to 7), or
    green if it isn't known."""
    if government is None:
        return GREEN
    return STATION_COLOURS[government & 7]


# ---- the station's tunnel, for launching and docking --------------------
#
# Each station has its own tunnel (Tunnel): the rings change shape from the
# mouth, which is the station's slot (a flat letterbox, level), through the
# station's own shape halfway in, to a hexagon at the bay doors; and the gaps
# between them follow a pattern. Every shape is the same 12 points in the
# same order round the centre, so a ring between two shapes is a blend.
TUNNEL_RINGS = 12
TUNNEL_FOCAL = 62.0     # pixels across a ring of radius 1 at distance 1
TUNNEL_SPIN = 0.5       # radians per second: the station turns as you go through
_CORNERS = 12


def _ngon(n):
    """A regular n-sided shape (n divides 12, a corner at 0 degrees), as 12
    points at 30 degree steps."""
    side = TAU / n
    out = []
    for k in range(_CORNERS):
        a = k * TAU / _CORNERS
        r = math.cos(side / 2) / math.cos(a % side - side / 2)
        out.append((r * math.cos(a), r * math.sin(a)))
    return tuple(out)


def _slot(aspect):
    """A station's slot: a rectangle aspect times as wide as it is tall, as 12
    points, three to each quarter (the middle of the side, half way up it,
    the corner), in the same order as _ngon's."""
    w = 1.0
    h = 1.0 / aspect
    out = []
    for sx, sy, flip in ((1, 1, False), (-1, 1, True), (-1, -1, False), (1, -1, True)):
        q = ((w, 0.0), (w, h * 0.5), (w, h)) if not flip else ((0.0, h), (w, h), (w, h * 0.5))
        out.extend((x * sx, y * sy) for x, y in q)
    return tuple(out)


HEXAGON = _ngon(6)
# the shape halfway along, which is what makes one station's tunnel unlike
# another's (the hexagon means straight from the slot to the doors)
TUNNEL_SHAPES = (
    ("HEXAGON", HEXAGON),
    ("SQUARE", _ngon(4)),
    ("TRIANGLE", _ngon(3)),
    ("ROUND", _ngon(12)),
)
SLOT_ASPECT = {"CORIOLIS": 3.0, "DODO": 2.0}    # from their slots' vertices: 20 x 60 and 32 x 64

# the gaps between rings, from the mouth inwards, before they are scaled to
# add up to TUNNEL_RINGS - 1 (so a tunnel is always the same length)
TUNNEL_SPACINGS = (
    ("EVEN", (1,) * 11),
    ("WIDENING", tuple(0.4 + 0.12 * i for i in range(11))),
    ("NARROWING", tuple(1.6 - 0.12 * i for i in range(11))),
    ("PAIRS", tuple(0.45 if i % 2 else 1.55 for i in range(11))),
    ("THREES", tuple(2.0 if i % 3 == 2 else 0.5 for i in range(11))),
)


def _blend(pa, pb, t):
    t = t * t * (3 - 2 * t)     # eased: the change is quickest in the middle
    return [(xa + (xb - xa) * t, ya + (yb - ya) * t) for (xa, ya), (xb, yb) in zip(pa, pb)]


class Tunnel:
    """One station's tunnel. Ring 0 is the mouth, the last ring the bay
    doors. depth[j] is ring j's distance in from the mouth (0 to
    TUNNEL_RINGS - 1); pts[j] its corners as whole numbers, 16ths of a pixel
    at distance 1. seed (the system's number) picks the middle shape and the
    spacing; station (its ship name) the slot's shape."""

    def __init__(self, seed=0, station="CORIOLIS"):
        seed = int(seed) & 0xFF
        mid = seed % len(TUNNEL_SHAPES)
        sp = (seed // 4 + seed // 17) % len(TUNNEL_SPACINGS)
        self.names = ("SLOT", TUNNEL_SHAPES[mid][0], "HEXAGON", TUNNEL_SPACINGS[sp][0])
        slot = _slot(SLOT_ASPECT.get(station, 3.0))
        middle = TUNNEL_SHAPES[mid][1]
        gaps = TUNNEL_SPACINGS[sp][1]
        k = (TUNNEL_RINGS - 1) / sum(gaps)
        last = TUNNEL_RINGS - 1
        self.depth = []
        self.pts = []
        d = 0.0
        scale = TUNNEL_FOCAL * 16
        for j in range(TUNNEL_RINGS):
            if j:
                d += gaps[j - 1] * k
            self.depth.append(d if j < last else float(last))
            t = 2.0 * j / last
            ring = _blend(slot, middle, t) if t <= 1.0 else _blend(middle, HEXAGON, t - 1.0)
            self.pts.append([(int(x * scale), int(y * scale)) for x, y in ring])
        self.mouth_in = 1.0 / SLOT_ASPECT.get(station, 3.0)   # the slot's half height, for the stars beyond


_TUNNEL0 = None


def _tunnel(tun):
    global _TUNNEL0
    if tun is not None:
        return tun
    if _TUNNEL0 is None:
        _TUNNEL0 = Tunnel(0)
    return _TUNNEL0


# where each corner of each ring lands on the screen, while drawing
_SX = [0] * (TUNNEL_RINGS * _CORNERS)
_SY = [0] * (TUNNEL_RINGS * _CORNERS)


def _draw_rings(ctx, tun, zs, t, rgb, doors):
    """Draw the rings at distances zs[j] (None if not drawn), the mouth and
    (if doors) the filled bay doors in the station's colour, and the long
    edges joining every other corner."""
    ctx.line_width = 1.5
    a = t * TUNNEL_SPIN
    ca = int(math.cos(a) * 1024)
    sa = int(math.sin(a) * 1024)
    sx = _SX
    sy = _SY
    last = TUNNEL_RINGS - 1
    shown = []
    for j in range(TUNNEL_RINGS):
        z = zs[j]
        if z is None:
            continue
        shown.append(j)
        kk = int(1024 / z)
        base = j * _CORNERS
        for i, (x, y) in enumerate(tun.pts[j]):
            sx[base + i] = ((x * ca - y * sa) >> 10) * kk >> 14
            sy[base + i] = ((x * sa + y * ca) >> 10) * kk >> 14
    # far to near, so the doors are filled before anything is drawn over them
    shown.sort(key=lambda j: -zs[j])
    for j in shown:
        z = zs[j]
        b = 1.0 if z < 2 else 2.0 / z      # dimmer further in
        base = j * _CORNERS
        if j == last and doors:
            ctx.rgb(rgb[0] * 0.25, rgb[1] * 0.25, rgb[2] * 0.25)
            _ring_path(ctx, base)
            ctx.fill()
        if j == 0 or (j == last and doors):
            ctx.rgb(rgb[0] * b, rgb[1] * b, rgb[2] * b)
        else:
            ctx.rgb(b, b, b)
        _ring_path(ctx, base)
        ctx.stroke()
    if len(shown) > 1:
        ctx.rgb(*GREY)
        ctx.begin_path()
        for i in range(0, _CORNERS, 2):
            first = True
            for j in shown:
                p = j * _CORNERS + i
                if first:
                    ctx.move_to(sx[p], sy[p])
                    first = False
                else:
                    ctx.line_to(sx[p], sy[p])
        ctx.stroke()


def _ring_path(ctx, base):
    sx = _SX
    sy = _SY
    ctx.begin_path()
    p = base + _CORNERS - 1
    ctx.move_to(sx[p], sy[p])
    for p in range(base, base + _CORNERS):
        ctx.line_to(sx[p], sy[p])


def _pulse(s, passes, mouth_flash):
    """How bright the LEDs are when s has gone past the first few of passes
    (the s at which each ring goes by, in order): a fading pulse after each
    ring, and after the mouth (the ring passes[mouth_flash], if any) a full
    flash fading to 0.4."""
    last = None
    for n, p in enumerate(passes):
        if s >= p:
            last = n
    if last is None:
        return 0.0
    p = s - passes[last]
    if last == mouth_flash:
        return 1.0 - 0.6 * min(1.0, p)
    return 0.5 * (1 - p) * (1 - p) if p < 1 else 0.0


def tunnel_glow(f, tun=None):
    """How bright the LEDs are at point f (0 to 1) of the launch: a pulse as
    each ring goes by, then full once you are out of the mouth."""
    tun = _tunnel(tun)
    last = TUNNEL_RINGS - 1
    s = (TUNNEL_RINGS + 1) * f * f
    if s >= last + 0.2:                 # out of the mouth
        return 1.0
    # ring j goes by when last - depth[j] + 0.6 - s = 0.4; the doors behind you don't
    passes = [last - tun.depth[j] + 0.2 for j in range(last - 1, -1, -1)]
    return _pulse(s, passes, None)


# Docking is the launch the other way round: in through the mouth, the rings
# coming at you more and more slowly, to a stop at the bay doors at the back.
DOCK_START = 3.0        # the mouth starts this far ahead
DOCK_END = 2.2          # and you stop with the doors this far ahead


def _dock_depth(f):
    """How far into the tunnel you are at point f (0 to 1) of docking:
    quick through the mouth, slowing to a stop."""
    g = 1.0 - f
    return (DOCK_START + TUNNEL_RINGS - 1 - DOCK_END) * (1.0 - g * g)


def dock_glow(f, tun=None):
    """How bright the LEDs are at point f (0 to 1) of docking: rising as the
    mouth comes up, a flash going through it, then a pulse for each ring."""
    tun = _tunnel(tun)
    s = _dock_depth(f)
    first = DOCK_START - 0.4            # the mouth goes by when s reaches this
    if s < first:
        return 0.4 * s / first
    return _pulse(s, [d + first for d in tun.depth], 0)


# stars seen through the mouth of the tunnel: (x, y, distance from the centre)
def _scatter(n):
    out = []
    v = 12345
    for _ in range(n):
        v = (v * 1103515245 + 12345) & 0x7FFFFFFF
        x = (v >> 8) % 221 - 110
        v = (v * 1103515245 + 12345) & 0x7FFFFFFF
        y = (v >> 8) % 221 - 110
        out.append((x, y, int(math.sqrt(x * x + y * y))))
    return tuple(out)


_STARS = _scatter(40)
_ZS = [None] * TUNNEL_RINGS


def draw_tunnel(ctx, f, t, rgb=GREEN, inward=False, tun=None):
    """The launch: flying out along the station's tunnel (tun, a Tunnel). f
    goes from 0 at the start to 1 at the mouth; t is the time in seconds,
    for the spin; rgb is the station's colour, for the mouth. With inward,
    docking instead: f goes from 0 outside the mouth to 1 stopped at the
    doors."""
    tun = _tunnel(tun)
    ctx.rgb(0, 0, 0).rectangle(-120, -120, 240, 240).fill()
    zs = _ZS
    last = TUNNEL_RINGS - 1
    if inward:
        s = _dock_depth(f)
        for j in range(TUNNEL_RINGS):
            z = tun.depth[j] + DOCK_START - s
            zs[j] = z if z >= 0.4 else None
        _draw_rings(ctx, tun, zs, t, rgb, True)
        return
    s = (TUNNEL_RINGS + 1) * f * f      # slow at first, then faster
    # space, through the mouth: as far out as the circle inside it
    z = 0.6 - s + last
    seen = 170 if z < 0.4 else int(tun.mouth_in * TUNNEL_FOCAL / z)
    if seen > 4:
        ctx.rgb(*GREY)
        for x, y, r in _STARS:
            if r < seen:
                ctx.rectangle(x, y, 1.5, 1.5).fill()
    for j in range(TUNNEL_RINGS):
        z = last - tun.depth[j] + 0.6 - s
        zs[j] = z if z >= 0.4 and j < last else None    # the doors are behind you
    _draw_rings(ctx, tun, zs, t, rgb, False)


def draw(ctx, g, ms=None, ring=True):
    """Draw the whole cockpit for Game g. ms is the frame time to show, or None."""
    draw_world(ctx, g)
    draw_hud(ctx, g, ms, ring)


def draw_world(ctx, g):
    """Space, ships, explosions and laser beams."""
    ctx.rgb(0, 0, 0).rectangle(-120, -120, 240, 240).fill()
    ctx.line_width = 1.5

    # stardust, so speed and turning can be seen; with the jump drive on, each
    # speck is a streak back to where it was two steps ago
    if g.jumping:
        ctx.rgb(*WHITE)
        tail = 2 * g.fwd
        if tail < STREAK_MIN:
            tail = STREAK_MIN
        ctx.begin_path()
        for p in g.dust:
            z = int(p[2])
            if z > INEAR:
                x = int(p[0])
                y = int(p[1])
                ctx.move_to(x * IFOCAL // z, -(y * IFOCAL // z))
                z += tail
                ctx.line_to(x * IFOCAL // z, -(y * IFOCAL // z))
        ctx.stroke()
    else:
        ctx.rgb(*GREY)
        for p in g.dust:
            z = int(p[2])
            if z > INEAR:
                ctx.rectangle(int(p[0]) * IFOCAL // z, -(int(p[1]) * IFOCAL // z), 1.5, 1.5).fill()

    if g.warp > 0:      # just out of hyperspace: streaks rushing out from the centre
        k = 256 - int(g.warp * 256 / _WARP_T)       # 0 at the start to 256 at the end
        if k < 0:
            k = 0
        ctx.rgb(*CYAN)
        ctx.begin_path()
        for dx, dy, r0 in _STREAKS:
            a = r0 + (k * 40 >> 8)
            b = a + 12 + (k * 110 >> 8)
            ctx.move_to(dx * a >> 8, dy * a >> 8)
            ctx.line_to(dx * b >> 8, dy * b >> 8)
        ctx.stroke()

    sp = g.space
    if sp is not None:
        _draw_space(ctx, sp)

    for o in g.objs:
        draw_ship(ctx, o)
        if o.beam > 0:
            _enemy_beam(ctx, o)

    for boom in g.booms:
        fade = 1.0 - boom.t / boom.life
        if fade <= 0:
            continue
        ctx.rgb(1, 0.5 + 0.5 * fade, 0.2 * fade)
        for p in boom.parts:
            z = int(p[2])
            if z > INEAR:
                ctx.rectangle(int(p[0]) * IFOCAL // z - 1,
                              -(int(p[1]) * IFOCAL // z) - 1, 2.5, 2.5).fill()

    if g.beam > 0:      # the player's twin lasers meet at the crosshair
        ctx.rgb(*RED)
        ctx.begin_path()
        ctx.move_to(-60, 100)
        ctx.line_to(0, 0)
        ctx.move_to(60, 100)
        ctx.line_to(0, 0)
        ctx.stroke()

    t = g.lock
    if t is not None and t.z > NEAR:    # missile sights: a diamond round the ship,
        k = FOCAL / t.z                 # red until the missile has locked on, then green
        h = int(t.bp.size * k) + 5
        if h < 9:
            h = 9
        elif h > 48:
            h = 48
        bx = int(t.x * k)
        by = -int(t.y * k)
        ctx.rgb(*(GREEN if g.locked else RED))
        ctx.begin_path()
        ctx.move_to(bx, by - h)
        ctx.line_to(bx + h, by)
        ctx.line_to(bx, by + h)
        ctx.line_to(bx - h, by)
        ctx.line_to(bx, by - h)
        ctx.stroke()


def _sphere(x, y, z, r):
    """Where a sphere is on screen: (x, y, radius) in pixels, or None if it is
    behind you, too close to draw as a circle, or off screen."""
    if z < r * 0.5:
        return None
    k = FOCAL / z
    x *= k
    y *= -k
    r *= k
    if x - r > 120 or x + r < -120 or y - r > 120 or y + r < -120:
        return None
    return x, y, r


def _covers(c):
    """True if the circle (x, y, r) covers the whole screen."""
    x = abs(c[0]) + 120
    y = abs(c[1]) + 120
    return x * x + y * y < c[2] * c[2]


MAX_ARC = 2000      # pixels: circles bigger than this that don't cover the screen are left out


def _draw_space(ctx, sp):
    """The sun (filled), the planet (an outline, filled black if it is in
    front of the sun) and the station, in its government's colour."""
    sun = _sphere(sp.sx, sp.sy, sp.sz, sp.sr)
    if sun is not None:
        ctx.rgb(*SUN)
        if _covers(sun):
            ctx.rectangle(-120, -120, 240, 240).fill()
        elif sun[2] < MAX_ARC:
            ctx.arc(sun[0], sun[1], sun[2], 0, TAU, 0).fill()
    planet = _sphere(sp.px, sp.py, sp.pz, sp.pr)
    if planet is not None and not _covers(planet) and planet[2] < MAX_ARC:
        x, y, r = planet
        if sun is not None:
            dx = x - sun[0]
            dy = y - sun[1]
            d = r + sun[2]
            if dx * dx + dy * dy < d * d:   # in front of the sun: hide it
                ctx.rgb(0, 0, 0).arc(x, y, r, 0, TAU, 0).fill()
        ctx.rgb(*PLANET).arc(x, y, r, 0, TAU, 0).stroke()
    if sp.station is not None:
        draw_ship(ctx, sp.station, station_colour(sp.gov))


# the compass, right of the radar: a ring with a dot that points to the planet,
# or to the station in the safe zone; bright if it is ahead, dim behind
COMPASS_X = 80
COMPASS_Y = 60
COMPASS_R = 9
_COMPASS = tuple(
    (COMPASS_X + COMPASS_R * math.cos(i * TAU / 16) - 0.75,
     COMPASS_Y + COMPASS_R * math.sin(i * TAU / 16) - 0.75)
    for i in range(16)
)


def _compass(ctx, sp):
    ctx.rgb(0.38, 0.38, 0.48)
    for px, py in _COMPASS:
        ctx.rectangle(px, py, 1.5, 1.5).fill()
    st = sp.station
    if sp.safe and st is not None:
        x, y, z = st.x, st.y, st.z
        rgb = station_colour(sp.gov)
    else:
        x, y, z = sp.px, sp.py, sp.pz
        rgb = PLANET
    d = math.sqrt(x * x + y * y + z * z)
    if d < 1:
        return
    k = (COMPASS_R - 2) / d
    dx = COMPASS_X + int(x * k)
    dy = COMPASS_Y - int(y * k)
    if z >= 0:
        ctx.rgb(*rgb).rectangle(dx - 1.5, dy - 1.5, 3, 3).fill()
    else:
        ctx.rgb(rgb[0] * 0.4, rgb[1] * 0.4, rgb[2] * 0.4).rectangle(dx - 1, dy - 1, 2, 2).fill()


def _vbar(ctx, x, frac, rgb):
    ctx.rgb(*DIM).rectangle(x, -30, 6, 60).fill()
    if frac > 0:
        if frac > 1:
            frac = 1
        h = 60 * frac
        ctx.rgb(*rgb).rectangle(x, 30 - h, 6, h).fill()


def draw_hud(ctx, g, ms=None, ring=True):
    """Crosshair, gauges, radar and numbers. ring: flash a red ring when hit
    (for badges where the LEDs can't be used for that)."""
    # crosshair
    ctx.rgb(*GREEN)
    ctx.rectangle(6, -0.75, 8, 1.5).fill()
    ctx.rectangle(-14, -0.75, 8, 1.5).fill()
    ctx.rectangle(-0.75, 6, 1.5, 8).fill()
    ctx.rectangle(-0.75, -14, 1.5, 8).fill()

    # gauges: speed and laser heat on the left, shield and energy on the right
    _vbar(ctx, -112, g.speed / 40.0, CYAN if g.jumping else GREEN)
    _vbar(ctx, -103, g.heat, RED if g.hot else YELLOW)
    _vbar(ctx, 97, g.shield / 100.0, CYAN)
    _vbar(ctx, 106, g.hull / 100.0, GREEN if g.hull > 30 else RED)

    # missiles left
    for i in range(4):
        ctx.rgb(*(GREEN if i < g.missiles else DIM)).rectangle(-70 + i * 9, 56, 6, 4).fill()

    # radar: across is left-right, up the ellipse is ahead, sticks show height
    ctx.rgb(0.38, 0.38, 0.48)
    for px, py in _RADAR:
        ctx.rectangle(px, py, 1.5, 1.5).fill()
    ctx.rectangle(-RADAR_RX, RADAR_Y - 0.5, 2 * RADAR_RX, 1).fill()
    ctx.rectangle(-0.5, RADAR_Y - RADAR_RY, 1, 2 * RADAR_RY).fill()
    for o in g.objs:        # in whole numbers, like draw_ship
        x = int(o.x)
        z = int(o.z)
        if x * x + z * z > _RADAR_R2:
            continue
        bx = x * RADAR_RX // _RADAR_R
        by = RADAR_Y - z * RADAR_RY // _RADAR_R
        h = int(o.y) * 22 // _RADAR_R
        if h > 22:
            h = 22
        elif h < -6:
            h = -6
        ctx.rgb(*(RED if o.kind == "ship" else COLOUR[o.kind]))   # hostile ships in red
        if h >= 0:
            ctx.rectangle(bx, by - h, 1, h).fill()
        else:
            ctx.rectangle(bx, by, 1, -h).fill()
        ctx.rectangle(bx - 1, by - h - 1, 3, 3).fill()
    sp = g.space
    if sp is not None:
        st = sp.station     # far off it is beyond cheap whole numbers, so check first
        if st is not None and -_RADAR_R < st.x < _RADAR_R and -_RADAR_R < st.z < _RADAR_R:
            x = int(st.x)
            z = int(st.z)
            if x * x + z * z <= _RADAR_R2:
                bx = x * RADAR_RX // _RADAR_R
                by = RADAR_Y - z * RADAR_RY // _RADAR_R
                h = int(max(-6000.0, min(22000.0, st.y))) * 22 // _RADAR_R
                ctx.rgb(*station_colour(sp.gov))
                if h >= 0:
                    ctx.rectangle(bx, by - h, 1, h).fill()
                else:
                    ctx.rectangle(bx, by, 1, -h).fill()
                ctx.rectangle(bx - 2, by - h - 2, 5, 5).fill()
        _compass(ctx, sp)

    # wave (yellow) and score (white), as one centred row of digits
    wave = str(g.wave)
    score = str(g.score)
    width = (len(wave) + len(score)) * 10 - 6 + 9
    x = -width / 2.0
    ctx.rgb(*YELLOW)
    x += number(ctx, g.wave, x, -102, _BIG, -1) + 9
    ctx.rgb(*WHITE)
    number(ctx, g.score, x, -102, _BIG, -1)
    if ms is not None:      # frame time in ms, small, just above
        ctx.rgb(*GREY)
        number(ctx, ms, 0, -114, _SMALL)

    if g.over:
        ctx.rgba(0, 0, 0, 0.8).rectangle(-100, -42, 200, 100).fill()
        text(ctx, "GAME OVER", 0, -22, 26, RED)
        text(ctx, "SCORE %d   KILLS %d" % (g.score, g.kills), 0, 6, 14, WHITE)
        text(ctx, g.again, 0, 30, 12, GREY)
        text(ctx, "F: title screen", 0, 46, 12, GREY)
    elif g.msg_t > 0:
        text(ctx, g.msg, 0, -66, 14, YELLOW)

    if ring and g.flash > 0:     # just been hit
        ctx.line_width = 6
        ctx.rgb(*RED).arc(0, 0, 116, 0, TAU, 0).stroke()
        ctx.line_width = 1.5
