"""Ace Commander for the EMF Tildagon / Spaceagon badge: a flight combat game and a
ship viewer, drawn as hidden-line wireframes with the original ship data.

Title screen
    Joystick left / right changes the choice, C (or press the joystick)
    selects it, F leaves the app. CONTINUE and NEW COMMANDER put a saved
    commander, whose kills earn a rank, in dock. COMBAT PRACTICE is the same
    fight with nothing kept.

Docked
    Joystick left / right changes screen (on the charts the joystick moves
    the cursor and E / B change screen). C launches from Status, and on a
    chart makes the selected system the hyperspace target. A launches from
    any screen. F goes back to Status, then to the title screen.

Flight (2026 Spaceagon)
    Joystick      pitch and roll
    C, or press the joystick    fire lasers (hold)
    D             fire a missile at the boxed target
    B             energy bomb (not in the game yet)
    F / E         speed up / slow down
    A             in-flight menu, which pauses the game
With tilt steering (chosen in the in-flight menu) tilting the badge steers and
the joystick still works; then a tap on D re-zeroes the tilt and holding D
fires the missile. The badge's LEDs flash red when you are hit.

In-flight menu
    Joystick up / down moves, C selects, F goes back, A resumes

Keyboard
    W / S or up / down pitch, A / D or left / right roll, E / Q speed up /
    down, F or SPACE lasers, M missile, P in-flight menu

Ship viewer
    Joystick or buttons turn the ship, CONFIRM (or N) next ship, P previous
    ship, CANCEL back to the title screen.

Ships come from ship_data.py, next to this file. Make it with
tools/import_ships.py from your own copy of the Elite source.

If something moves the wrong way, or you want other buttons, change the
constants below.
"""
import asyncio
import gc
import time

import app
from events.input import BUTTON_TYPES, ButtonDownEvent, ButtonUpEvent
from system.eventbus import eventbus

from . import bench, cockpit, commander, docked, game, title, viewer

try:
    import imu
except ImportError:
    imu = None

try:
    from events.keyboard import KEYBOARD_BUTTONS
except ImportError:
    KEYBOARD_BUTTONS = {}

try:
    from .ship_data import SHIPS
except Exception:
    SHIPS = ()

try:    # the galaxy's starting numbers, made by tools/import_galaxy.py
    from . import game_data
except Exception:
    game_data = None

try:    # the joystick's directions and buttons A-F, told apart from each other
    from events.joystick import JOYSTICK_BUTTON_TYPES as JOY
    from frontboards.common import FRONTBOARD_BUTTON_TYPES as FB
except ImportError:
    JOY = FB = None     # older firmware: fall back to the plain direction buttons

try:    # lets the flight screen skip the 50 ms pause the badge adds to every frame
    from system.scheduler import scheduler
except Exception:
    scheduler = None

try:    # the badge's LEDs, used to flash red when you're hit
    from tildagonos import tildagonos
    from system.patterndisplay.events import PatternDisable, PatternEnable
except Exception:
    tildagonos = None

# Flight buttons (letters as printed on the badge)
BTN_SPEED_UP = "F"
BTN_SPEED_DOWN = "E"
BTN_MISSILE = "D"      # with tilt steering: tap to re-zero the tilt, hold to fire
BTN_BOMB = "B"
BTN_MENU = "A"
HOLD_MISSILE = 0.45    # seconds D must be held to fire a missile when steering by tilt

# Tilt control. Values are in g (1 g = 9.81 m/s^2). The axis directions are a
# first guess, so flip the signs if things tilt the wrong way.
TILT_DEFAULT = False    # True to start with tilt steering instead of the joystick
TILT_DEADZONE = 0.10    # ignore small wobbles around the neutral pose
TILT_FULL = 0.50        # tilt that gives full rate (about 30 degrees)
TILT_PITCH_SIGN = 1.0
TILT_ROLL_SIGN = 1.0
TILT_SWAP_AXES = False  # True if the badge's x axis should pitch, y roll

INVERT_PITCH = False    # False: joystick up raises the nose. True: up dives.
SHOW_FRAME_TIME = True  # small frame time (ms) at the top of the flight screen
USE_LEDS = True         # flash the badge's LEDs red when hit (else a ring on screen)
FAST_FRAMES = True      # on the title screen, in flight and in the viewer, don't wait
                        # 50 ms between frames
PERF_LOG = 60           # print a PERF line to the console every this many flight
                        # frames (0 = never)
LAUNCH_TIME = 2.5       # seconds of launch tunnel (C skips it)
DOCK_TIME = 3.0         # seconds of flying in through the station's tunnel on docking (C skips it)
DOCK_NAMES = {"A": "AUTHENTIC", "B": "ASSISTED", "C": "AUTOMATIC"}
DEBUG_MENU = True       # show DEBUG, a page of cheats for testing, in the in-flight menu
PERF_SLOW = 25          # ms over the average that makes a frame count as slow

# the app's folder; the commander is saved there
try:
    APP_DIR = __file__.rsplit("/", 1)[0]
    if not APP_DIR.startswith("/") and ":" not in APP_DIR:
        APP_DIR = "/" + APP_DIR         # the badge gives a path relative to /
except Exception:
    APP_DIR = "/apps/ace_commander"
SAVE_PATH = APP_DIR + "/commander.json"
_build = None


def build():
    """The build number from metadata.json next to this file (deploy.bat copies
    it with the code), read once, or "?" if it can't be read."""
    global _build
    if _build is None:
        try:
            import json
            with open(APP_DIR + "/metadata.json") as f:
                _build = str(json.load(f)["version"])
        except Exception:
            _build = "?"
    return _build

LEVEL_NOTE = ("fewer, weaker enemies", "the standard fight", "more, deadlier enemies")
ANY = ("up", "down", "left", "right")           # joystick, buttons or arrow keys
STICK = ("j_up", "j_down", "j_left", "j_right")  # the joystick only
ARROWS = ("k_UP", "k_DOWN", "k_LEFT", "k_RIGHT")

try:
    _us = time.ticks_us
    _ms = time.ticks_ms
    _diff = time.ticks_diff
except AttributeError:   # off the badge (tests)
    def _us():
        return int(time.perf_counter() * 1000000)

    def _ms():
        return int(time.perf_counter() * 1000)

    def _diff(a, b):
        return a - b


def _tilt(v):
    """Turn a tilt in g into -1..1, with a dead zone."""
    a = abs(v) - TILT_DEADZONE
    if a <= 0:
        return 0.0
    a = min(1.0, a / (TILT_FULL - TILT_DEADZONE))
    return a if v > 0 else -a


class Pad:
    """Which controls are down, kept up to date from button events.

    The firmware's own Buttons.get() searches every known button each time it
    is asked, which is slow when a game asks about twenty things a frame. This
    does the matching once per press and then answers from a set.

    Controls are named: "up" / "confirm" / ... for any button of that kind,
    "j_up" ... for the joystick only, "A" to "F" for the lettered buttons and
    "k_W" ... for keyboard keys.
    """

    def __init__(self, owner):
        table = [(name.lower(), BUTTON_TYPES[name])
                 for name in ("UP", "DOWN", "LEFT", "RIGHT", "CONFIRM", "CANCEL")]
        if JOY is not None:
            for name in ("UP", "DOWN", "LEFT", "RIGHT"):
                table.append(("j_" + name.lower(), JOY[name]))
            for name in "ABCDEF":
                table.append((name, FB[name]))
        for name in ("W", "S", "A", "D", "E", "Q", "F", "M", "P", "N", "SPACE",
                     "UP", "DOWN", "LEFT", "RIGHT"):
            key = KEYBOARD_BUTTONS.get(name)
            if key is not None:
                table.append(("k_" + name, key))
        self.table = table
        self.names = {}      # button -> the control names it counts as
        self.pressed = {}    # button -> True while it is down
        self.count = {}      # control name -> how many buttons hold it down
        self.hits = set()    # control names pressed since the last end_frame()
        eventbus.on(ButtonDownEvent, self._down, owner)
        eventbus.on(ButtonUpEvent, self._up, owner)

    def _names(self, button):
        names = self.names.get(button)
        if names is None:
            names = tuple(n for n, kind in self.table if kind in button)
            self.names[button] = names
        return names

    def _down(self, event):
        button = event.button
        if self.pressed.get(button):
            return      # already down (key repeat)
        self.pressed[button] = True
        count = self.count
        for n in self._names(button):
            count[n] = count.get(n, 0) + 1
            self.hits.add(n)

    def _up(self, event):
        button = event.button
        if not self.pressed.get(button):
            return
        self.pressed[button] = False
        count = self.count
        for n in self._names(button):
            if count.get(n, 0) > 0:
                count[n] -= 1

    def held(self, name):
        return self.count.get(name, 0) > 0

    def hit(self, name):
        """True if the control was pressed since the last frame."""
        return name in self.hits

    def end_frame(self):
        if self.hits:
            self.hits = set()

    def clear(self):
        self.pressed = {}
        self.count = {}
        self.hits = set()


class AceCommander(app.App):
    def __init__(self):
        super().__init__()
        self.pad = Pad(self)
        # menu (the title screen) | level | docked | launch | flight | viewer | bench
        self.mode = "menu"
        self.bench = None
        self.menu = 0           # which title screen choice is showing
        self.title = title.Title(SHIPS) if SHIPS else None
        self.cmdr = commander.load(SAVE_PATH)   # the saved commander, or None
        self.level = 1          # the level showing on the new-commander screen
        self.game = None
        self.career = False     # the current game counts towards the commander
        self.docked = True      # the commander is at a station, not out in a flight
        self.banked = 0         # this game's kills already given to the commander
        self.saved_over = False
        self.fmenu = 0          # which in-flight menu line is highlighted
        self.fsub = None        # None, "status", "chart", "pause" or "debug": the menu page
        self.galaxy = None      # made the first time a commander flies
        self.chart = None       # the local chart, while it is open
        self.jump_to = None     # (system, distance) the hyperspace countdown is for
        self.dt = 0.0           # seconds since the last update
        self.dock = None        # the docked screens (docked.Docked), while docked
        self.target = None      # the system picked for the next hyperspace jump
        self.launch_t = 0.0     # how far into the launch tunnel
        self.dock_t = 0.0       # how far into the docking tunnel
        self.tunnel = None      # this station's tunnel (cockpit.Tunnel), for launching and docking
        self.d_t = None         # how long D has been down, when steering by tilt
        self.viewer = None
        self.paused = False
        self.tilt_ok = imu is not None   # the accelerometer is there and working
        # steer by tilt as well as the joystick. Without the joystick's own
        # button names (older firmware) tilt is the default, as the direction
        # buttons are then needed for speed and missiles.
        self.use_tilt = self.tilt_ok and (TILT_DEFAULT or JOY is None)
        self.zero = None                 # accelerometer reading taken as "level"
        self.ms = 0.0                    # smoothed frame time
        self.leds_on = False             # we have taken over the LEDs
        self.led_level = None            # (r, g, b) last written, 0-255 each
        self.glow = 0.0                  # the station's colour fading on the LEDs after launch
        # smoothed time, in ms, spent in the game rules and in drawing calls
        self.t_step = 0.0
        self.t_world = 0.0
        self.t_hud = 0.0
        self.fast = FAST_FRAMES and scheduler is not None
        # totals for the PERF line
        self.p_n = 0
        self.p_frame = 0.0
        self.p_max = 0.0
        self.p_update = 0.0
        self.p_rules = 0.0
        self.p_draw = 0.0
        self.p_avg = 60.0   # last window's average frame, ms
        self.p_nslow = 0
        self.p_slow = []
        self.p_skip = True  # the first flight frame follows a menu frame

    # ---- frame loop -------------------------------------------------------

    async def run(self, render_update):
        """The badge's own loop waits 50 ms after every update before the next
        one, which caps any app at about 14 frames a second. On the title
        screen, in flight and in the viewer this asks for a redraw directly and
        carries on as soon as it has been drawn. The other screens, and
        anything unexpected, use the normal path."""
        last = _ms()
        while True:
            now = _ms()
            self.update(_diff(now, last))
            last = now
            if self.fast and (self.mode == "flight" or self.mode == "viewer"
                              or self.mode == "launch" or self.mode == "docking"
                              or (self.mode == "menu" and self.title is not None)):
                try:
                    scheduler.render_needed.set()
                    await asyncio.sleep(0)      # the renderer and other tasks run here
                    if scheduler.app_is_foregrounded(self):
                        continue
                except Exception:
                    self.fast = False           # firmware differs: stay on the normal path
            await render_update()

    # ---- LEDs -------------------------------------------------------------

    def _leds(self, level):
        """Set the ring of 12 LEDs to red at level 0-255."""
        self._leds_rgb(level, 0, 0)

    def _leds_rgb(self, r, g, b):
        """Set the ring of 12 LEDs to (r, g, b), 0-255 each, taking them over
        from the badge's own pattern first. Only writes when the colour changes."""
        if tildagonos is None or not USE_LEDS:
            return
        try:
            if not self.leds_on:
                eventbus.emit(PatternDisable())
                try:
                    tildagonos.set_led_power(True)
                except Exception:
                    pass
                self.leds_on = True
                self.led_level = None
            rgb = (r, g, b)
            if rgb != self.led_level:
                self.led_level = rgb
                leds = tildagonos.leds
                for i in range(1, 13):      # the front ring is LEDs 1 to 12
                    leds[i] = rgb
                leds.write()
        except Exception:
            self.leds_on = False

    def _leds_release(self):
        """Hand the LEDs back to the badge's own pattern."""
        if self.leds_on:
            self.leds_on = False
            try:
                eventbus.emit(PatternEnable())
            except Exception:
                pass

    def _leds_flight(self):
        g = self.game
        if self.paused:
            self._leds(0)
        elif g.over:
            self._leds(48)
        elif g.flash > 0.18:    # full, then fading over the 0.3 s after a hit
            self._leds(255)
        elif g.flash > 0.09:
            self._leds(160)
        elif g.flash > 0:
            self._leds(80)
        elif self.glow > 0:     # just launched: the station's colour fades away
            self._leds_station(self.glow)
            self.glow -= self.dt * 2.0
        else:
            self._leds(0)

    def _government(self):
        """The government of the commander's system (0 to 7), or None."""
        c = self.cmdr
        if c is None or c.system is None or self.galaxy is None:
            return None
        return self.galaxy.system(c.system).government

    def _leds_station(self, k):
        """The LEDs in this station's colour, k (0 to 1) of full brightness,
        in 16 steps so they are written only when the step changes."""
        k = int(k * 15 + 0.5)
        rgb = cockpit.station_colour(self._government())
        self._leds_rgb(int(rgb[0] * 255) * k // 15, int(rgb[1] * 255) * k // 15,
                       int(rgb[2] * 255) * k // 15)

    # ---- steering ---------------------------------------------------------

    def _steer(self, tilt, dirs, arrows):
        """(roll, pitch) as -1..1 each, from tilt, the four direction controls
        named in dirs, and the keyboard. Pitch is negative for up."""
        held = self.pad.held
        jx = jy = 0.0
        if tilt and self.tilt_ok:
            try:
                ax, ay, _ = imu.acc_read()
            except Exception:
                self.tilt_ok = False
                self.use_tilt = False
            else:
                ax /= 9.81
                ay /= 9.81
                if self.zero is None:
                    self.zero = (ax, ay)
                dx = ax - self.zero[0]
                dy = ay - self.zero[1]
                if TILT_SWAP_AXES:
                    dx, dy = dy, dx
                jx = TILT_ROLL_SIGN * _tilt(dx)
                jy = TILT_PITCH_SIGN * _tilt(dy)
        if dirs is not None:
            if held(dirs[0]):
                jy -= 1.0
            if held(dirs[1]):
                jy += 1.0
            if held(dirs[2]):
                jx -= 1.0
            if held(dirs[3]):
                jx += 1.0
        if arrows:      # not already counted in dirs
            if held("k_UP"):
                jy -= 1.0
            if held("k_DOWN"):
                jy += 1.0
            if held("k_LEFT"):
                jx -= 1.0
            if held("k_RIGHT"):
                jx += 1.0
        if held("k_W"):
            jy -= 1.0
        if held("k_S"):
            jy += 1.0
        if held("k_A"):
            jx -= 1.0
        if held("k_D"):
            jx += 1.0
        return max(-1.0, min(1.0, jx)), max(-1.0, min(1.0, jy))

    # ---- update -----------------------------------------------------------

    def update(self, delta):
        t0 = _us()
        if not self.paused:     # the pause menu shows it, so keep it still there
            self.ms += (delta - self.ms) * 0.1
        pad = self.pad
        hit = pad.hit
        cancel = hit("cancel")
        confirm = hit("confirm")
        self.dt = dt = delta / 1000.0

        if self.mode == "menu":
            if cancel:
                pad.clear()
                self.minimise()
            elif SHIPS:     # nothing to choose without ship_data.py
                if self.title is None:
                    self.title = title.Title(SHIPS)
                items = self.choices()
                if self.menu >= len(items):
                    self.menu = 0
                if hit("left") or hit("up"):
                    self.menu = (self.menu - 1) % len(items)
                elif hit("right") or hit("down"):
                    self.menu = (self.menu + 1) % len(items)
                elif confirm:
                    self._choose(items[self.menu])
                self.title.step(dt)
        elif self.mode == "level":
            if cancel:
                self.mode = "menu"
            elif hit("left") or hit("up"):
                self.level = (self.level - 1) % len(commander.LEVELS)
            elif hit("right") or hit("down"):
                self.level = (self.level + 1) % len(commander.LEVELS)
            elif confirm:
                self.cmdr = commander.Commander(self.level)
                self.cmdr.save(SAVE_PATH)
                self.menu = 0       # CONTINUE is now the first choice
                self._dock()
        elif self.mode == "docked":
            self._update_docked(dt, cancel, confirm)
        elif self.mode == "launch":
            self.launch_t += dt
            self._leds_station(cockpit.tunnel_glow(min(1.0, self.launch_t / LAUNCH_TIME),
                                                   self.tunnel))
            if confirm or self.launch_t >= LAUNCH_TIME:
                self.glow = 1.0
                if self.game is not None:
                    self.game.over = True   # every launch is a new flight
                self.docked = False
                self._start_flight(True)
        elif self.mode == "docking":
            self.dock_t += dt
            self._leds_station(cockpit.dock_glow(min(1.0, self.dock_t / DOCK_TIME), self.tunnel))
            if confirm or self.dock_t >= DOCK_TIME:
                self._leds_release()
                self._dock()
        elif self.mode == "viewer":
            self._update_viewer(dt, cancel, confirm)
        elif self.mode == "bench":
            if cancel:
                self.mode = "menu"
                self.bench = None
            else:
                self.bench.step(delta)
        else:
            flying = self._update_flight(dt, cancel, confirm)
            if self.mode == "flight":
                self._leds_flight()
            else:
                self._leds_release()
            if flying and PERF_LOG:
                self._perf(delta, _diff(_us(), t0) / 1000.0)
        pad.end_frame()

    def choices(self):
        """The title screen's choices, in order."""
        items = ["NEW COMMANDER", "COMBAT PRACTICE", "SHIP VIEWER", "SPEED TEST"]
        if self.cmdr is not None:
            items.insert(0, "CONTINUE")
        return items

    def _choose(self, item):
        if item == "CONTINUE":
            g = self.game
            if self.docked or g is None or g.over or not self.career:
                self._dock()
            else:               # a flight that was left part-way carries on
                self._start_flight(True)
        elif item == "NEW COMMANDER":
            self.level = 1
            self.mode = "level"
        elif item == "COMBAT PRACTICE":
            self._start_flight(False)
        elif item == "SHIP VIEWER":
            self.zero = None
            if self.viewer is None:
                self.viewer = viewer.Viewer(SHIPS)
            self.mode = "viewer"
        else:
            self.bench = bench.Bench()
            self.mode = "bench"

    def _start_flight(self, career):
        """Go into flight: as the commander, or for practice. A game that was
        left part-way carries on if it is the same kind."""
        level = self.cmdr.level if career else 1
        if not career:
            self.glow = 0.0
        g = self.game
        fresh = True
        if g is None:
            self.game = g = game.Game(SHIPS, level)
            self.banked = 0
        elif g.over or career != self.career or g.level != level:
            g.set_level(level)
            g.reset()
            self.banked = 0
        else:
            fresh = False
        self.career = career
        g.again = "C: back to the station" if career else "C: fly again"
        if career:
            self._place()
            if fresh or g.space is None:
                self._enter_space("launch")
        self.jump_to = None
        g.jump_t = 0.0
        self.saved_over = False
        self.zero = None
        self.d_t = None
        self.paused = False
        self.fsub = None
        self.mode = "flight"

    def _place(self):
        """Make the galaxy, and put a commander who is nowhere yet at the
        starting system. Without game_data.py there is no galaxy."""
        if game_data is None:
            return
        if self.galaxy is None:
            from . import galaxy
            self.galaxy = galaxy.Galaxy(game_data.SEEDS, game_data.PAIRS)
        c = self.cmdr
        if c.system is None:
            c.system = self.galaxy.find(*game_data.START_XY) or 0
            c.fuel = min(commander.MAX_FUEL, game_data.START_FUEL)

    def _enter_space(self, how):
        """Put the game in the commander's star system: "launch" (from the
        station) or "arrive" (from hyperspace). Without a galaxy there is no
        system, and the flight is open space with waves of ships."""
        c = self.cmdr
        if self.galaxy is None or c is None or c.system is None:
            return
        self.game.set_docking(c.docking)
        s = self.galaxy.system(c.system)
        self.game.enter(s.radius, "DODO" if s.tech >= game.DODO_TECH else "CORIOLIS", how,
                        s.government)

    def _dock(self):
        """Show the commander docked at their star system. Docking fills the
        tank (for nothing, until there is money to be made) and saves."""
        self._bank()            # before career is set: practice kills don't count
        self._place()
        self.career = True
        c = self.cmdr
        c.fuel = commander.MAX_FUEL
        c.save(SAVE_PATH)
        if self.game is not None:
            self.game.docked = False
        if self.target == c.system:
            self.target = None
        self.dock = docked.Docked(c, self.galaxy, self.target)
        self.docked = True
        self.jump_to = None
        self.paused = False
        self.fsub = None
        self.chart = None
        self.mode = "docked"

    def _update_docked(self, dt, cancel, confirm):
        """The docked tabs. The joystick changes tab, except on the charts,
        where it moves the cursor and E / B change tab instead. C launches
        from Status and picks a target on a chart. A launches from any tab.
        F goes back to Status, and from Status to the title screen."""
        d = self.dock
        hit = self.pad.hit
        new = FB is not None
        if cancel:
            if d.tab:
                d.go(0)
            else:
                self.mode = "menu"
            return
        if new and hit(BTN_MENU):
            self._launch()
            return
        if new and hit(BTN_SPEED_DOWN):
            d.go(d.tab - 1)
            return
        if new and hit(BTN_BOMB):
            d.go(d.tab + 1)
            return
        if d.view is not None:
            if confirm:
                d.pick()
                self.target = d.target
            else:
                jx, jy = self._steer(False, STICK if new else ANY, new)
                d.view.move(jx, jy, dt)
            return
        if hit("j_left" if new else "left"):
            d.go(d.tab - 1)
        elif hit("j_right" if new else "right"):
            d.go(d.tab + 1)
        elif confirm and d.name() == "STATUS":
            self._launch()

    def _pick_tunnel(self):
        """Each station's tunnel has its own shapes and spacing, from its system."""
        c = self.cmdr
        seed = 0
        station = "CORIOLIS"
        if c is not None and c.system is not None:
            seed = c.system
            if self.galaxy is not None and self.galaxy.system(seed).tech >= game.DODO_TECH:
                station = "DODO"
        self.tunnel = cockpit.Tunnel(seed, station)

    def _launch(self):
        self._pick_tunnel()
        self.launch_t = 0.0
        self.dock = None
        self.mode = "launch"

    def _jump_ok(self):
        """The hyperspace target, if the fuel will reach it: (system, distance)."""
        if self.target is None or self.galaxy is None or not self.career:
            return None
        d = self.galaxy.distance(self.cmdr.system, self.target)
        if 0 < d <= self.cmdr.fuel:
            return self.target, d
        return None

    def _arrive(self):
        """The game has just jumped: move the commander and use the fuel."""
        g = self.game
        g.jumped = False
        to = self.jump_to
        self.jump_to = None
        c = self.cmdr
        if to is None or not self.career or c is None or self.galaxy is None:
            return
        c.system = to[0]
        c.fuel = max(0, c.fuel - to[1])
        self.target = None
        self._save()
        self._enter_space("arrive")
        g.say(self.galaxy.system(c.system).name, 3.0)

    def _bank(self):
        """Give the commander the kills made since the last call."""
        g = self.game
        if g is not None and self.career and self.cmdr is not None and g.kills > self.banked:
            rank = self.cmdr.add_kills(g.kills - self.banked)
            self.banked = g.kills
            if rank and not g.over:
                g.say("RANK: " + rank, 3.0)

    def _save(self):
        if self.career and self.cmdr is not None:
            self._bank()
            self.cmdr.save(SAVE_PATH)

    def _to_title(self):
        self._save()
        self.paused = False
        self.fsub = None
        self.mode = "menu"

    def _perf(self, delta, update_ms):
        """Every PERF_LOG flight frames, print where the time went.

        A frame counts as slow if it took PERF_SLOW ms longer than the last
        window's average; the line lists how long each slow one took (up to
        8). Nothing here forces a garbage collection, so a slow frame is the
        badge's own doing. Printing and reading the heap size take a while,
        so the frame after each line is left out."""
        if self.p_skip:
            self.p_skip = False
            return
        self.p_n += 1
        self.p_frame += delta
        if delta > self.p_max:
            self.p_max = delta
        if delta > self.p_avg + PERF_SLOW:
            self.p_nslow += 1
            if len(self.p_slow) < 8:
                self.p_slow.append(int(delta))
        self.p_update += update_ms
        if self.p_n >= PERF_LOG:
            n = self.p_n
            self.p_avg = self.p_frame / n
            t0 = _us()
            used = getattr(gc, "mem_alloc", lambda: 0)()
            free = getattr(gc, "mem_free", lambda: 0)()
            mem_ms = _diff(_us(), t0) / 1000.0
            print("PERF frame %.0f max %.0f update %.1f rules %.1f draw %.1f objs %d fast %d"
                  " slow %d %s used %d free %d memcall %.1f" % (
                      self.p_avg, self.p_max, self.p_update / n, self.p_rules / n,
                      self.p_draw / n, len(self.game.objs), 1 if self.fast else 0,
                      self.p_nslow, self.p_slow, used, free, mem_ms))
            self.p_n = 0
            self.p_nslow = 0
            self.p_slow = []
            self.p_skip = True
            self.p_frame = self.p_max = self.p_update = self.p_rules = self.p_draw = 0.0

    def _update_viewer(self, dt, cancel, confirm):
        v = self.viewer
        pad = self.pad
        if cancel:
            self.mode = "menu"
            return
        if confirm or pad.hit("k_N"):
            v.switch(1)
            self.zero = None
        elif pad.hit("k_P"):
            v.switch(-1)
            self.zero = None
        jx, jy = self._steer(self.use_tilt, ANY, False)
        v.step(dt, jx, jy)

    def _update_flight(self, dt, cancel, confirm):
        """Returns True if the game moved on a step."""
        pad = self.pad
        hit = pad.hit
        held = pad.held
        g = self.game
        new = FB is not None    # joystick and lettered buttons are told apart
        menu = hit(BTN_MENU) if new else cancel
        if hit("k_P"):
            menu = True

        if g.over:
            if not self.saved_over:
                self.saved_over = True
                self._save()
            if confirm and self.career:
                self._dock()
            elif confirm:
                g.reset()
                self.jump_to = None
                self.banked = 0
                self.saved_over = False
                self.zero = None
            elif cancel:
                self._to_title()
            else:
                g.step(dt, 0.0, 0.0, 0.0, False, False)   # let the wreckage drift
            return False
        if self.paused:
            self._update_fmenu(new, menu, cancel, confirm)
            return False
        if menu:
            self.paused = True
            self.fmenu = 0
            self.fsub = None
            self.d_t = None
            return False

        tilt = self.use_tilt and self.tilt_ok
        throttle = 0.0
        missile = False
        if new:                 # joystick steers; lettered buttons do the rest
            if held(BTN_SPEED_UP):
                throttle += 1.0
            if held(BTN_SPEED_DOWN):
                throttle -= 1.0
            if not tilt:
                self.d_t = None
                if hit(BTN_MISSILE):
                    missile = True
            else:               # a tap on D re-zeroes the tilt, holding it fires
                if hit(BTN_MISSILE):
                    self.d_t = 0.0
                if self.d_t is not None:
                    if held(BTN_MISSILE):
                        self.d_t += dt
                        if self.d_t >= HOLD_MISSILE:
                            missile = True
                            self.d_t = None
                    else:
                        self.zero = None
                        self.d_t = None
            if hit(BTN_BOMB):
                g.say("NO ENERGY BOMB")
            roll, jy = self._steer(tilt, STICK, True)
        elif tilt:              # older firmware: the d-pad is free for speed and missiles
            if held("up"):
                throttle += 1.0
            if held("down"):
                throttle -= 1.0
            if hit("left"):
                missile = True
            if hit("right"):
                self.zero = None
            roll, jy = self._steer(True, None, False)
        else:
            roll, jy = self._steer(False, ANY, False)
        if held("k_E"):
            throttle += 1.0
        if held("k_Q"):
            throttle -= 1.0
        if hit("k_M"):
            missile = True
        fire = held("confirm") or held("k_F") or held("k_SPACE")
        pitch = jy if INVERT_PITCH else -jy
        t0 = _us()
        g.step(dt, pitch, roll, max(-1.0, min(1.0, throttle)), fire, missile)
        rules = _diff(_us(), t0) / 1000.0
        self.t_step += (rules - self.t_step) * 0.1
        self.p_rules += rules
        if g.jumped:
            self._arrive()
        if self.career:
            self._bank()
            if g.docked:        # flown in: the tunnel, then the docked screens
                g.docked = False
                self._bank()
                self.dock_t = 0.0
                self._pick_tunnel()
                self.mode = "docking"
        return True

    # ---- in-flight menu ---------------------------------------------------

    def _fitems(self):
        """The lines on the current page of the in-flight menu."""
        if self.fsub == "pause":
            stick = "JOYSTICK" if FB is not None else "STICK"
            if self.tilt_ok:
                steer = "STEER: " + ("TILT" if self.use_tilt else stick)
            else:
                steer = "STEER: %s (no tilt sensor)" % stick
            items = [steer]
            if self.career and self.cmdr is not None:
                items.append("DOCKING: " + DOCK_NAMES[self.cmdr.docking])
            items.append("QUIT TO TITLE")
            return items
        if self.fsub == "debug":
            return ["INVINCIBLE: " + ("ON" if self.game.god else "OFF"),
                    "KILL ALL ENEMIES", "NEXT WAVE", "ADD AN ENEMY",
                    "REFILL MISSILES", "REFILL FUEL", "+10 KILLS", "TO THE STATION",
                    "DOCK NOW"]
        return self._fitems_top()

    def _debug(self, item):
        """Do one of the DEBUG page's cheats. Kills made this way count like
        any others, so with a commander they raise the rank."""
        g = self.game
        if item.startswith("INVINCIBLE"):
            g.god = not g.god
            if g.god:
                g.shield = g.hull = 100.0
        elif item == "KILL ALL ENEMIES":
            for o in list(g.objs):
                if o.kind == "ship" or o.kind == "missile":
                    g._kill(o)
        elif item == "NEXT WAVE":
            g.wave += 1
            g.wave_kills = 0
            g.say("WAVE %d" % g.wave, 2.5)
        elif item == "ADD AN ENEMY":
            if len(g.objs) + len(g.new) < game.MAX_OBJECTS:
                g._spawn_ship()
        elif item == "REFILL MISSILES":
            if g.missile_bp:
                g.missiles = 4
        elif item == "REFILL FUEL":
            if self.career and self.cmdr is not None:
                self.cmdr.fuel = commander.MAX_FUEL
        elif item == "+10 KILLS":
            g.kills += 10
        elif item == "TO THE STATION":
            g.to_station()
        elif item == "DOCK NOW":
            if self.career and self.cmdr is not None:
                self._dock()
                return
        if self.career:
            self._bank()

    def _resume(self):
        self.paused = False
        self.fsub = None
        self.zero = None

    def _update_fmenu(self, new, menu, cancel, confirm):
        hit = self.pad.hit
        if menu and new:        # A closes the menu from any page
            self._resume()
            return
        if self.fsub == "status":
            if confirm or cancel:
                self.fsub = None
            return
        if self.fsub == "chart":
            self._update_chart(new, cancel, confirm)
            return
        items = self._fitems()
        if hit("j_up" if new else "up"):
            self.fmenu = (self.fmenu - 1) % len(items)
        elif hit("j_down" if new else "down"):
            self.fmenu = (self.fmenu + 1) % len(items)
        elif cancel:
            if self.fsub is None:
                self._resume()
            else:
                self.fmenu = self._fitems_top().index(
                    "DEBUG" if self.fsub == "debug" else "PAUSE MENU")
                self.fsub = None
        elif self.fsub == "pause":
            left = hit("j_left" if new else "left")
            side = left or hit("j_right" if new else "right")
            item = items[self.fmenu]
            if self.fmenu == 0:
                if (confirm or side) and self.tilt_ok:
                    self.use_tilt = not self.use_tilt
                    self.zero = None
            elif item.startswith("DOCKING"):
                if confirm or side:     # C and right go A, B, C, A; left goes back
                    c = self.cmdr
                    i = commander.DOCKING.index(c.docking) + (-1 if left else 1)
                    c.docking = commander.DOCKING[i % len(commander.DOCKING)]
                    self.game.set_docking(c.docking)
                    c.save(SAVE_PATH)
            elif confirm:
                self._to_title()
        elif self.fsub == "debug":
            if confirm:
                self._debug(items[self.fmenu])
        elif confirm:
            item = items[self.fmenu]
            if item == "RESUME":
                self._resume()
            elif item == "STATUS":
                self.fsub = "status"
            elif item == "LOCAL CHART":
                from . import chart
                self.chart = chart.Chart(self.galaxy, self.cmdr.system, self.cmdr.fuel,
                                         self.target)
                self.fsub = "chart"
            elif item.startswith("JUMP"):
                to = self._jump_ok()
                if to is not None:
                    self.jump_to = to
                    self.game.start_jump()
                self._resume()
            elif item == "DEBUG":
                self.fsub = "debug"
                self.fmenu = 0
            else:
                self.fsub = "pause"
                self.fmenu = 0

    def _update_chart(self, new, cancel, confirm):
        """The local chart: the joystick moves the cursor, C jumps to the
        selected system if the fuel will reach it, F goes back."""
        if cancel:
            self.fmenu = self._fitems_top().index("LOCAL CHART")
            self.fsub = None
            self.chart = None
            return
        ch = self.chart
        if confirm:
            to = ch.target()
            if to is not None:
                self.jump_to = to
                self.target = to[0]
                self.game.start_jump()
                self.chart = None
                self._resume()
            return
        jx, jy = self._steer(False, STICK if new else ANY, new)
        ch.move(jx, jy, self.dt)

    def _fitems_top(self):
        items = ["RESUME"]
        if self.career:
            to = self._jump_ok()
            if to is not None and self.game is not None and self.game.jump_t <= 0:
                items.append("JUMP: " + self.galaxy.system(to[0]).name)
            if self.galaxy is not None:
                items.append("LOCAL CHART")
            items.append("STATUS")
        items.append("PAUSE MENU")
        if DEBUG_MENU:
            items.append("DEBUG")
        return items

    # ---- draw -------------------------------------------------------------

    def draw(self, ctx):
        ctx.save()
        if self.mode == "viewer":
            self.viewer.draw(ctx)
        elif self.mode == "bench":
            self.bench.draw(ctx)
        elif self.mode == "flight":
            if self.paused and self.fsub == "chart":
                self.chart.draw(ctx)
                ctx.restore()
                return
            t0 = _us()
            cockpit.draw_world(ctx, self.game)
            t1 = _us()
            cockpit.draw_hud(ctx, self.game, self.ms if SHOW_FRAME_TIME else None,
                             not self.leds_on)
            if self.paused:
                self._draw_fmenu(ctx)
            else:
                world = _diff(t1, t0) / 1000.0
                hud = _diff(_us(), t1) / 1000.0
                self.t_world += (world - self.t_world) * 0.1
                self.t_hud += (hud - self.t_hud) * 0.1
                self.p_draw += world + hud
        elif self.mode == "level":
            self._draw_level(ctx)
        elif self.mode == "docked":
            self.dock.draw(ctx)
        elif self.mode == "launch":
            cockpit.draw_tunnel(ctx, min(1.0, self.launch_t / LAUNCH_TIME), self.launch_t,
                                cockpit.station_colour(self._government()), False, self.tunnel)
        elif self.mode == "docking":
            cockpit.draw_tunnel(ctx, min(1.0, self.dock_t / DOCK_TIME), self.dock_t,
                                cockpit.station_colour(self._government()), True, self.tunnel)
        else:
            self._draw_menu(ctx)
        ctx.restore()

    def _draw_menu(self, ctx):
        if not SHIPS or self.title is None:
            text = cockpit.text
            ctx.rgb(0, 0, 0).rectangle(-120, -120, 240, 240).fill()
            text(ctx, "ACE", 0, -74, 34, cockpit.WHITE)
            text(ctx, "COMMANDER", 0, -46, 18, cockpit.WHITE)
            if not SHIPS:
                text(ctx, "ship_data.py", 0, -10, 16, cockpit.YELLOW)
                text(ctx, "not found", 0, 10, 16, cockpit.YELLOW)
                text(ctx, "copy it next to app.py", 0, 36, 12, cockpit.GREY)
            return
        items = self.choices()
        self.title.draw(ctx, items[self.menu % len(items)])

    def _draw_level(self, ctx):
        text = cockpit.text
        ctx.rgb(0, 0, 0).rectangle(-120, -120, 240, 240).fill()
        text(ctx, "NEW COMMANDER", 0, -70, 18, cockpit.WHITE)
        text(ctx, "LEVEL", 0, -34, 13, cockpit.GREY)
        ctx.rgb(*cockpit.DIM).rectangle(-80, -24, 160, 30).fill()
        text(ctx, "< %s >" % commander.LEVELS[self.level], 0, -9, 20, cockpit.GREEN)
        text(ctx, LEVEL_NOTE[self.level], 0, 20, 12, cockpit.YELLOW)
        top = commander.thresholds(self.level)[-1]
        text(ctx, "top rank at %d kills" % top, 0, 36, 12, cockpit.YELLOW)
        if self.cmdr is not None:
            text(ctx, "replaces your saved commander", 0, 58, 11, cockpit.RED)
        text(ctx, "C: start   F: back", 0, 80, 12, cockpit.GREY)

    def _draw_fmenu(self, ctx):
        text = cockpit.text
        ctx.rgba(0, 0, 0, 0.85).rectangle(-100, -80, 200, 142).fill()
        if self.fsub == "status":
            c = self.cmdr
            text(ctx, "COMMANDER", 0, -60, 18, cockpit.WHITE)
            text(ctx, c.rank(), 0, -34, 16, cockpit.GREEN)
            text(ctx, "%d kills   level: %s" % (c.kills, commander.LEVELS[c.level]),
                 0, -12, 12, cockpit.YELLOW)
            nxt = c.next_rank()
            if nxt:
                text(ctx, "%d more for %s" % (nxt[1], nxt[0]), 0, 6, 12, cockpit.YELLOW)
            if self.galaxy is not None:
                text(ctx, "%s   fuel %d.%d LY" % (
                    self.galaxy.system(c.system).name, c.fuel // 10, c.fuel % 10),
                    0, 23, 12, cockpit.CYAN)
            text(ctx, "this flight: %d kills, wave %d" % (self.game.kills, self.game.wave),
                 0, 38, 11, cockpit.GREY)
            text(ctx, "C or F: back", 0, 53, 11, cockpit.GREY)
            return
        if self.fsub == "debug":
            ctx.rgba(0, 0, 0, 0.85).rectangle(-100, 62, 200, 18).fill()
            text(ctx, "DEBUG - BUILD " + build(), 0, -66, 16, cockpit.RED)
            for i, label in enumerate(self._fitems()):
                y = -50 + i * 13
                if i == self.fmenu:
                    ctx.rgb(*cockpit.DIM).rectangle(-96, y - 6.5, 192, 13).fill()
                text(ctx, label, 0, y, 12, cockpit.GREEN if i == self.fmenu else cockpit.GREY)
            text(ctx, "C: do it   F: back", 0, 70, 11, cockpit.GREY)
            return
        pause = self.fsub == "pause"
        text(ctx, "PAUSE MENU" if pause else "PAUSED", 0, -60, 18, cockpit.WHITE)
        items = self._fitems()
        for i, label in enumerate(items):
            y = -40 + i * 16
            if i == self.fmenu:
                ctx.rgb(*cockpit.DIM).rectangle(-96, y - 8, 192, 16).fill()
            text(ctx, label, 0, y, 13, cockpit.GREEN if i == self.fmenu else cockpit.GREY)
        if pause:
            # where the time goes: whole frame, game rules, and my drawing calls
            text(ctx, "frame %d  rules %d" % (self.ms, self.t_step), 0, 20, 11, cockpit.GREY)
            text(ctx, "world %d  hud %d  objs %d" % (
                self.t_world, self.t_hud, len(self.game.objs)), 0, 34, 11, cockpit.GREY)
            text(ctx, "C: change   F: back", 0, 52, 11, cockpit.GREY)
        else:
            text(ctx, "C: choose   A: resume", 0, 54, 11, cockpit.GREY)


__app_export__ = AceCommander
