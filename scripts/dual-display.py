#!/usr/bin/env python3
"""
Dual Display for Volumio.

Volumio drives a single display out of the box: the kiosk opens on the
first output and the second one stays unused. This script lays out the
outputs and brings up a browser window of its own on the second display.

On x86 it can also turn that display into a MilkDrop visualizer driven
by the audio actually playing. projectMSDL has no ARM builds, so on
Raspberry Pi the plugin works as a second display only.

  - The front panel keeps the regular Volumio kiosk.
  - The second display, when attached, gets its own browser window.
  - Attaching and detaching is picked up on the fly.
  - The touchscreen is bound to the panel so taps do not land on the
    neighbouring display once the desktop is extended.

Visualizer controls, x86 only:

  - Double tap on the panel turns it on. It shows up on the second
    display when one is attached, otherwise on the panel itself.
  - Single tap moves to the next preset. With the visualizer on the panel
    a tap anywhere will do. With it on the second display, while the panel
    shows the Volumio interface, only taps inside a set zone count -
    the left third by default, where the album art sits.
  - Another double tap turns it off.

Media keys are left alone: the Volumio kiosk picks them up itself through
the Media Session API, and handling them here would fire every press twice.

The panel is picked by physical size, which xrandr reports in millimetres:
a built-in panel is always smaller than a television or a monitor. Output
order in the list does not match the physical connectors and differs from
board to board, so it is not used. Outputs can be set explicitly in the
plugin settings.
"""

import os
import re
import signal
import struct
import subprocess
import sys
import threading
import time

# ------------------------------------------------------------------ settings

# Plugin directory. index.js writes the conf here on start and on every
# settings save in the Volumio interface.
PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(PLUGIN_DIR, "dual-display.conf")

DEFAULTS = {
    "PROJECTM": "/usr/bin/projectMSDL",
    "TOUCH_DEV": "",            # empty - look it up
    "DOUBLE_TAP_TIME": "0.3",
    "AUDIO_DEVICE": "2",
    # plughw, not hw: the ALSA plug layer converts the sample format.
    # Whichever side opens the loopback first locks the format, and with
    # bare hw the other one cannot adapt - aplay dies with
    # "Sample format non available".
    "LOOPBACK_OUT": "plughw:1,0",
    "VOLUMIO_FIFO": "/tmp/stream.mp3",
    "PRIMARY_OUTPUT": "",       # empty - work it out
    "SECONDARY_OUTPUT": "",     # empty - work it out
    # Always the stock Volumio interface. It shows the album art on its
    # own, and offering another plugin here only confuses things: if that
    # plugin is not installed the user gets a blank screen.
    "SECOND_SCREEN_URL": "http://localhost:3000",
    "CHROMIUM_PROFILE": "/data/volumiokiosk-tv",
    "NEXT_KEY": "n",
    "POLL_INTERVAL": "2.0",
    # Picture settings, passed to projectMSDL on the command line
    "AUTO_CHANGE": "false",
    "PRESET_DURATION": "30",
    "TRANSITION_DURATION": "3",
    "BEAT_SENSITIVITY": "1",
    "FPS": "60",
    # Zone on the panel where a tap moves to the next preset while the
    # visualizer runs on the second display. Fractions of panel width
    # and height, 0 to 1.
    "TAP_ZONE_X1": "0.0",
    "TAP_ZONE_X2": "0.33",
    "TAP_ZONE_Y1": "0.0",
    "TAP_ZONE_Y2": "1.0",
}


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG):
        with open(CONFIG) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                cfg[key.strip()] = val.strip().strip('"').strip("'")
    return cfg


CFG = load_config()

DOUBLE_TAP_TIME = float(CFG["DOUBLE_TAP_TIME"])
POLL_INTERVAL = float(CFG["POLL_INTERVAL"])


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ----------------------------------------------------------------------- X11

def find_xauth():
    """X authority file. The name changes on every start."""
    try:
        files = [f for f in os.listdir("/tmp") if f.startswith("serverauth.")]
        if not files:
            return None
        files.sort(key=lambda f: os.path.getmtime("/tmp/" + f), reverse=True)
        return "/tmp/" + files[0]
    except OSError:
        return None


def x_env():
    env = dict(os.environ)
    env["DISPLAY"] = ":0"
    xauth = find_xauth()
    if xauth:
        env["XAUTHORITY"] = xauth
    return env


def run(cmd):
    return subprocess.run(cmd, env=x_env(), capture_output=True, text=True)


def x_owner():
    """
    Who owns the X session.

    On x86 Volumio images X runs as root, on Raspberry Pi as volumio.
    The browser has to run as the same user: the authority file belongs
    to them, and another process would get "Authorization required"
    and fail to open the display.
    """
    xauth = find_xauth()
    if not xauth:
        return "root"
    try:
        uid = os.stat(xauth).st_uid
        import pwd
        return pwd.getpwuid(uid).pw_name
    except (OSError, KeyError):
        return "root"


def pointer_position():
    """
    Where the pointer is, in desktop pixels.
    The touchscreen is bound to the panel and moves the pointer on touch,
    so this is the point that was touched.
    """
    res = run(["xdotool", "getmouselocation"])
    m = re.search(r"x:(\d+)\s+y:(\d+)", res.stdout)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


# ------------------------------------------------------ input device lookup

# Codes from the Linux kernel
BTN_TOUCH = 0x14a

EV_KEY = 0x01


def parse_input_devices():
    """Parses /proc/bus/input/devices."""
    devices = []
    current = None

    try:
        with open("/proc/bus/input/devices") as f:
            for line in f:
                line = line.rstrip("\n")

                if line.startswith("I:"):
                    current = {"name": "", "phys": "", "handlers": [],
                               "key": "", "abs": ""}
                    devices.append(current)

                elif current is None:
                    continue

                elif line.startswith("N: Name="):
                    current["name"] = line.split("=", 1)[1].strip('"')

                elif line.startswith("P: Phys="):
                    current["phys"] = line.split("=", 1)[1].strip()

                elif line.startswith("H: Handlers="):
                    current["handlers"] = line.split("=", 1)[1].split()

                elif line.startswith("B: KEY="):
                    current["key"] = line.split("=", 1)[1].strip()

                elif line.startswith("B: ABS="):
                    current["abs"] = line.split("=", 1)[1].strip()

    except OSError as e:
        log(f"cannot read the device list: {e}")
        return []

    return devices


def has_bit(mask_str, bit):
    """
    Checks a bit in a mask from /proc/bus/input/devices.
    The mask is groups of 64 bits, most significant first, space separated.
    """
    if not mask_str:
        return False

    words = mask_str.split()
    words.reverse()

    word_index = bit // 64
    bit_index = bit % 64

    if word_index >= len(words):
        return False

    try:
        value = int(words[word_index], 16)
    except ValueError:
        return False

    return bool(value & (1 << bit_index))


def event_path(dev):
    """Path to /dev/input/eventN."""
    for h in dev["handlers"]:
        if h.startswith("event"):
            return "/dev/input/" + h
    return None


def find_touch_devices():
    """Touchscreens: BTN_TOUCH plus absolute coordinates."""
    found = []
    for dev in parse_input_devices():
        if has_bit(dev["key"], BTN_TOUCH) and dev["abs"]:
            path = event_path(dev)
            if path and os.path.exists(path):
                found.append((path, dev["name"]))
    return found


# ------------------------------------------------------------------ outputs

def get_outputs():
    """
    Connected outputs.

    active=True  - a mode is assigned, the output can show a picture.
    active=False - the cable is in but the output is not enabled yet.
                   This happens when a display is attached on the fly.

    mm_w, mm_h - physical size in millimetres, used to tell a built-in
                 panel from an external display.
    """
    res = run(["xrandr"])
    outputs = []
    current = None

    for line in res.stdout.splitlines():
        # Mode lines are indented and follow their output. The one marked
        # with a plus is the preferred mode - the display's native
        # resolution.
        if current is not None and line.startswith(" "):
            mode = re.match(r"\s+(\d+x\d+)\S*\s", line)
            if mode and "+" in line and not current["preferred"]:
                current["preferred"] = mode.group(1)
            continue

        m = re.match(
            r"^(\S+)\s+connected\s+(primary\s+)?(\d+)x(\d+)\+(\d+)\+(\d+)", line)
        if m:
            o = {
                "name": m.group(1),
                "primary": bool(m.group(2)),
                "active": True,
                "w": int(m.group(3)),
                "h": int(m.group(4)),
                "x": int(m.group(5)),
                "y": int(m.group(6)),
                "mm_w": 0,
                "mm_h": 0,
            }
        elif re.match(r"^(\S+)\s+connected", line):
            o = {
                "name": line.split()[0],
                "primary": False,
                "active": False,
                "w": 0, "h": 0, "x": 0, "y": 0,
                "mm_w": 0, "mm_h": 0,
            }
        else:
            current = None
            continue

        mm = re.search(r"(\d+)mm x (\d+)mm", line)
        if mm:
            o["mm_w"] = int(mm.group(1))
            o["mm_h"] = int(mm.group(2))

        o["preferred"] = ""
        outputs.append(o)
        current = o

    return outputs


def monitor_index(output_name):
    """
    Monitor number for the --monitor option of projectMSDL.
    SDL numbers monitors by their place on the desktop, so sort by
    coordinates rather than by the order xrandr lists them.
    """
    outs = sorted([o for o in get_outputs() if o["active"]],
                  key=lambda o: (o["x"], o["y"]))
    for i, o in enumerate(outs, start=1):
        if o["name"] == output_name:
            return i
    return 1


# --------------------------------------------------------------- visualizer

def preset_duration():
    """
    How long a preset stays on screen, in seconds.

    projectMSDL has no option to lock presets, so with automatic change
    turned off they are held in place by a duration of a full day.
    A tap still moves to the next one.
    """
    if CFG["AUTO_CHANGE"].lower() in ("1", "true", "yes"):
        return str(CFG["PRESET_DURATION"])
    return "86400"


def has_projectm():
    """
    Whether projectMSDL is installed.

    There are no ARM builds, so there is no visualizer on Raspberry Pi.
    The plugin then works as a second display only.
    """
    return os.path.exists(CFG["PROJECTM"])


class Visualizer:
    def __init__(self):
        self.proc = None
        self.aplay = None
        self.output = None

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, output_name):
        if self.is_running():
            return

        self.aplay = subprocess.Popen(
            ["aplay", "-D", CFG["LOOPBACK_OUT"],
             "-f", "S16_LE", "-r", "44100", "-c", "2",
             CFG["VOLUMIO_FIFO"]],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        time.sleep(1)

        mon = monitor_index(output_name)

        # Everything is passed on the command line rather than written into
        # projectMSDL.properties: that file belongs to the projectMSDL
        # package, and the user may have settings of their own in it.
        cmd = [
            CFG["PROJECTM"],
            "-d", str(CFG["AUDIO_DEVICE"]),
            "--monitor", str(mon),
            "--presetPath", os.path.join(PLUGIN_DIR, "presets"),
            "--texturePath", os.path.join(PLUGIN_DIR, "textures"),
            "--fullscreen", "1",
            "--exclusive", "0",
            "--enableSplash", "0",
            # Zero size - the window takes the size of its display
            "--width", "0",
            "--height", "0",
            "--presetDuration", preset_duration(),
            "--transitionDuration", str(CFG["TRANSITION_DURATION"]),
            "--beatSensitivity", str(CFG["BEAT_SENSITIVITY"]),
            "--fps", str(CFG["FPS"]),
            "--shuffleEnabled", "0",
        ]

        self.proc = subprocess.Popen(
            cmd, env=x_env(),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        self.output = output_name
        log(f"visualizer started on {output_name} (monitor {mon})")

    def stop(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

        if self.aplay:
            self.aplay.terminate()
            try:
                self.aplay.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.aplay.kill()
            self.aplay = None

        self.output = None
        log("visualizer stopped")

    def next_preset(self):
        if not self.is_running():
            return
        run(["xdotool", "search", "--class", "projectM",
             "key", "--window", "%1", CFG["NEXT_KEY"]])
        self.raise_window()
        log("next preset")

    def raise_window(self):
        """
        Raises the visualizer window.
        A mouse click hands focus to the kiosk, which then comes up on
        top when the visualizer runs on the same panel.
        """
        if not self.is_running():
            return
        run(["xdotool", "search", "--class", "projectM",
             "windowraise", "%1"])


# ------------------------------------------------------------ second display

def chromium_binary():
    """Browser executable name - it differs between images."""
    for name in ("chromium", "chromium-browser"):
        if subprocess.run(["which", name], capture_output=True).returncode == 0:
            return name
    return "chromium"


class SecondScreen:
    """
    Chromium with the Volumio interface on the second display.

    Runs as whoever owns the X session: the authority file belongs to
    them and another process would not open the display. The browser
    profile has to belong to the same user for the same reason.
    """

    def __init__(self):
        self.proc = None

    def is_running(self):
        """
        Whether the browser is alive.

        Checks not only our own process but Chromium itself: it may be
        started through sudo, and when X restarts it dies while the
        wrapper stays. The Volumio kiosk also restarts X when other
        plugins are enabled or disabled, taking our window with it.
        """
        if self.proc is None or self.proc.poll() is not None:
            return False

        res = subprocess.run(
            ["pgrep", "-f", "user-data-dir=" + CFG["CHROMIUM_PROFILE"]],
            capture_output=True)
        return res.returncode == 0

    def prepare_profile(self, user):
        """The profile has to belong to whoever owns the X session."""
        profile = CFG["CHROMIUM_PROFILE"]
        try:
            os.makedirs(profile, exist_ok=True)
            subprocess.run(["chown", "-R", f"{user}:{user}", profile],
                           capture_output=True)
        except OSError as e:
            log(f"cannot prepare profile: {e}")

    def start(self, x, y, w, h):
        if self.is_running():
            return

        user = x_owner()
        self.prepare_profile(user)

        env = x_env()
        home = "/root" if user == "root" else f"/home/{user}"

        args = [
            chromium_binary(), "--kiosk", "--no-sandbox",
            f"--user-data-dir={CFG['CHROMIUM_PROFILE']}",
            f"--window-position={x},{y}",
            f"--window-size={w},{h}",
            "--disable-session-crashed-bubble",
            "--disable-infobars",
            "--no-first-run",
            "--disable-translate",
            "--autoplay-policy=no-user-gesture-required",
            CFG["SECOND_SCREEN_URL"],
        ]

        if user == "root":
            cmd = args
            run_env = env
        else:
            cmd = ["sudo", "-u", user, "env",
                   "DISPLAY=" + env["DISPLAY"],
                   "XAUTHORITY=" + env.get("XAUTHORITY", ""),
                   "HOME=" + home] + args
            run_env = None

        self.proc = subprocess.Popen(
            cmd, env=run_env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log(f"second display: {w}x{h} at {x},{y} (as {user})")

    def stop(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

        # If the browser was started through sudo, terminate kills the
        # wrapper and Chromium stays. Finish it off by profile name.
        subprocess.run(
            ["pkill", "-f", "user-data-dir=" + CFG["CHROMIUM_PROFILE"]],
            capture_output=True)

        log("second display closed")


# --------------------------------------------------------------------- input

EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)


class InputWatcher(threading.Thread):
    """
    Reads an input device and calls back on a press.
    The button code is passed along so touch can be told from a mouse.
    The thread ends if the device goes away.
    """

    def __init__(self, device, name, callback, want_code):
        super().__init__(daemon=True)
        self.device = device
        self.name = name
        self.callback = callback
        self.want_code = want_code
        self.alive = True

    def run(self):
        try:
            with open(self.device, "rb") as f:
                while self.alive:
                    data = f.read(EVENT_SIZE)
                    if not data or len(data) < EVENT_SIZE:
                        break
                    _, _, etype, code, value = struct.unpack(EVENT_FORMAT, data)
                    if etype == EV_KEY and value == 1 and code == self.want_code:
                        self.callback(self.want_code)
        except (OSError, IOError):
            pass

        self.alive = False
        log(f"device disconnected: {self.name}")


# ------------------------------------------------------------------- control

class Controller:
    def __init__(self):
        self.vis = Visualizer()
        self.second = SecondScreen()
        self.last_press = 0.0
        self.pending = None
        self.lock = threading.Lock()
        self.toggle_lock = threading.Lock()
        self.right_button_off = False
        self.watchers = {}

    # ---- displays

    def detect_screens(self):
        """Returns (panel, second). The second one may be None."""
        outs = get_outputs()
        if not outs:
            return None, None

        names = [o["name"] for o in outs]

        want_primary = CFG["PRIMARY_OUTPUT"]
        want_secondary = CFG["SECONDARY_OUTPUT"]


        if want_primary and want_primary in names:
            panel = want_primary
        else:
            # Connectors that only ever carry a built-in panel. Some of
            # their drivers report no physical size at all, so the name
            # has to be checked before anything else.
            builtin = ("DSI", "DPI", "LVDS", "EDP")

            panels = [o for o in outs
                      if o["name"].upper().startswith(builtin)]

            if panels:
                panel = panels[0]["name"]
            else:
                # Otherwise the panel is the output with the smallest
                # physical area. xrandr reports the size in millimetres,
                # and a built-in display is always smaller than a
                # television or a monitor. Output order in the list is no
                # good: it differs from board to board and does not match
                # the physical connectors.
                def area(o):
                    if o["mm_w"] > 0 and o["mm_h"] > 0:
                        return o["mm_w"] * o["mm_h"]
                    # No size reported - treat as large so it is not
                    # mistaken for the panel
                    return 10 ** 9

                panel = min(outs, key=area)["name"]

        # Only an output with a cable in it counts as the second display.
        # It may not be enabled yet - that is fine, layout_screens
        # will bring it up.
        second = None
        if want_secondary and want_secondary in names and want_secondary != panel:
            second = want_secondary
        else:
            for n in names:
                if n != panel:
                    second = n
                    break

        return panel, second

    def layout_screens(self, panel, second):
        """
        Panel at the origin and primary, the second one to its right.

        Outputs get --auto: when a display is attached on the fly X does
        not always enable the output by itself, and without this it stays
        without a mode or picks a non-native resolution.
        """
        outs = {o["name"]: o for o in get_outputs()}
        if panel not in outs:
            return

        # Enable the panel first and find out how wide it is
        self.enable_output(panel, outs[panel], ["--pos", "0x0", "--primary"])
        self.clear_margins(panel)

        outs = {o["name"]: o for o in get_outputs()}
        pw = outs.get(panel, {}).get("w") or 1600

        if second and second in outs:
            # --rotate normal because Touch Display rotates whichever
            # output is primary at the time, and at boot that can still
            # be the second display. The panel is left alone: rotating it
            # is up to Touch Display or Pi Screen Setup.
            self.enable_output(second, outs[second],
                               ["--pos", f"{pw}x0", "--rotate", "normal"],
                               force_mode=True)
            self.clear_margins(second)
        else:
            # No second display - switch the other outputs off so no
            # stray desktop area is left hanging
            for name, o in outs.items():
                if name != panel and o["active"]:
                    run(["xrandr", "--output", name, "--off"])

        self.map_touch_to_panel(panel)

    def enable_output(self, name, info, extra, force_mode=False):
        """
        Places an output, enabling it first if it is not active yet.

        --auto is only used on an output that has no mode at all: it
        resets rotation too, and other tools set that up. Pi Screen Setup,
        for one, rotates a portrait panel through the kernel command line,
        and running --auto over a working output would undo it.

        force_mode asks for the preferred mode explicitly - the native
        resolution the display reports. Rearranging outputs sometimes
        leaves the second one on a fallback mode, and --mode sets it back
        without touching rotation.
        """
        cmd = ["xrandr", "--output", name]

        if not info.get("active"):
            cmd.append("--auto")
        elif force_mode and info.get("preferred"):
            current = f"{info['w']}x{info['h']}"
            # Under rotation width and height are swapped, so compare
            # both ways round before forcing anything
            swapped = f"{info['h']}x{info['w']}"
            if info["preferred"] not in (current, swapped):
                cmd += ["--mode", info["preferred"]]

        cmd += extra
        run(cmd)

    def clear_margins(self, output):
        """
        Clears the black border around the picture.

        The KMS driver on Raspberry Pi sets non-zero margins on an
        output - this is underscan, a leftover from analogue television.
        The picture shrinks and a black band, usually 48 pixels, is left
        around it. Other platforms may not have these properties, so
        errors are ignored.
        """
        for side in ("left", "right", "top", "bottom"):
            run(["xrandr", "--output", output, "--set", f"{side} margin", "0"])

    def map_touch_to_panel(self, panel):
        """
        Binds touchscreens to the panel.
        Recognised by the calibration matrix or by device path - xinput
        ids change from boot to boot.
        """
        touch_nodes = {path for path, _ in find_touch_devices()}

        res = run(["xinput", "list"])
        for line in res.stdout.splitlines():
            if "slave  pointer" not in line:
                continue
            m = re.search(r"id=(\d+)", line)
            if not m:
                continue
            dev_id = m.group(1)

            props = run(["xinput", "list-props", dev_id]).stdout

            is_touch = "Calibration Matrix" in props
            if not is_touch:
                node = re.search(r'Device Node \(\d+\):\s+"([^"]+)"', props)
                if node and node.group(1) in touch_nodes:
                    is_touch = True

            if is_touch:
                run(["xinput", "map-to-output", dev_id, panel])
                log(f"touchscreen id={dev_id} bound to {panel}")

    def pointer_ids(self):
        """xinput ids of mice and other pointers, touchscreens excluded."""
        ids = []
        res = run(["xinput", "list"])
        for line in res.stdout.splitlines():
            if "slave  pointer" not in line:
                continue
            m = re.search(r"id=(\d+)", line)
            if not m:
                continue
            dev_id = m.group(1)

            props = run(["xinput", "list-props", dev_id]).stdout
            # Leave touchscreens alone
            if "Calibration Matrix" in props:
                continue
            # Only pointing devices have buttons
            if "Button Labels" not in props and "Middle Emulation" not in props:
                continue
            ids.append(dev_id)
        return ids

    def set_right_button(self, enabled):
        """
        Turns the right mouse button off while the visualizer is on screen,
        and back on afterwards.

        A right click on the visualizer hands focus to the Volumio kiosk,
        which then comes up on top; on a bare desktop it brings up the
        Openbox context menu instead. Outside the visualizer the button
        is none of our business, so it is only taken away for that time.
        """
        mapping = ["1", "2", "3"] if enabled else ["1", "2", "0"]
        for dev_id in self.pointer_ids():
            run(["xinput", "set-button-map", dev_id] + mapping)
        self.right_button_off = not enabled
        log("right button " + ("restored" if enabled else "disabled"))

    # ---- presses

    def on_press(self, source):
        now = time.time()
        with self.lock:
            if now - self.last_press < DOUBLE_TAP_TIME:
                self.last_press = 0.0
                if self.pending:
                    self.pending.cancel()
                    self.pending = None
                threading.Thread(target=self.toggle, daemon=True).start()
            else:
                self.last_press = now
                if self.pending:
                    self.pending.cancel()
                self.pending = threading.Timer(
                    DOUBLE_TAP_TIME, self.single, args=(source,))
                self.pending.start()

    def single(self, source):
        with self.lock:
            self.pending = None

        if not self.vis.is_running():
            return

        panel, second = self.detect_screens()

        # Visualizer on the panel itself - no interface underneath,
        # a tap anywhere will do.
        if self.vis.output == panel:
            self.vis.next_preset()
            return

        # Visualizer on the second display while the panel shows the
        # Volumio interface. Only taps inside the set zone count - where
        # the album art sits - so presses on the player controls do not
        # skip the picture along.
        if self.in_tap_zone(panel):
            self.vis.next_preset()

    def in_tap_zone(self, panel):
        """Whether the touch landed inside the preset zone on the panel."""
        px, py = pointer_position()
        if px is None:
            return False

        outs = {o["name"]: o for o in get_outputs()}
        o = outs.get(panel)
        if not o or not o["w"] or not o["h"]:
            return False

        # Coordinates relative to the panel
        rx = (px - o["x"]) / o["w"]
        ry = (py - o["y"]) / o["h"]

        x1 = float(CFG["TAP_ZONE_X1"])
        x2 = float(CFG["TAP_ZONE_X2"])
        y1 = float(CFG["TAP_ZONE_Y1"])
        y2 = float(CFG["TAP_ZONE_Y2"])

        return x1 <= rx <= x2 and y1 <= ry <= y2

    def toggle(self):
        # No projectMSDL, no visualizer - the plugin then works
        # as a second display only
        if not has_projectm():
            return

        # A lock so two quick presses do not start two instances
        if not self.toggle_lock.acquire(blocking=False):
            return
        try:
            if self.vis.is_running():
                self.vis.stop()
                self.set_right_button(True)
                panel, second = self.detect_screens()
                if second:
                    outs = {o["name"]: o for o in get_outputs()}
                    if second in outs:
                        o = outs[second]
                        self.second.start(o["x"], o["y"], o["w"], o["h"])
            else:
                panel, second = self.detect_screens()
                target = second if second else panel
                if second:
                    self.second.stop()
                    time.sleep(0.5)
                self.vis.start(target)
                self.set_right_button(False)
        finally:
            self.toggle_lock.release()

    # ---- input devices

    def refresh_inputs(self):
        """Picks up new devices and drops the ones that went away."""


        for path in list(self.watchers):
            if not self.watchers[path].alive:
                del self.watchers[path]

        wanted = []

        touch_cfg = CFG["TOUCH_DEV"]
        if touch_cfg:
            if os.path.exists(touch_cfg):
                wanted.append((touch_cfg, "touchscreen (from config)", BTN_TOUCH))
        else:
            for path, name in find_touch_devices():
                wanted.append((path, name, BTN_TOUCH))

        for path, name, code in wanted:
            if path in self.watchers:
                continue
            w = InputWatcher(path, name, self.on_press, code)
            w.start()
            self.watchers[path] = w
            log(f"touchscreen: {name} ({path})")

    # ---- main loop

    def panel_geometry(self, panel):
        """
        Size of the panel as it is right now.

        Rotating a portrait panel swaps its width and height, and the
        second display then has to move: it sits to the right of the
        panel, at the panel's width. Touch Display and xrandr can both
        rotate it at any moment, so this is watched rather than assumed.
        """
        for o in get_outputs():
            if o["name"] == panel and o["active"]:
                return (o["w"], o["h"])
        return None

    def loop(self):
        prev_second = None
        prev_geometry = None
        first = True

        while True:
            self.refresh_inputs()

            # The visualizer may have died on its own rather than by a tap -
            # give the right button back in that case too
            if self.right_button_off and not self.vis.is_running():
                self.set_right_button(True)

            panel, second = self.detect_screens()
            geometry = self.panel_geometry(panel) if panel else None

            # The browser may have died with X: the Volumio kiosk
            # restarts it when other plugins are enabled or disabled.
            # Bring it back up.
            if (not first and second and second == prev_second
                    and not self.vis.is_running()
                    and not self.second.is_running()):
                outs = {o["name"]: o for o in get_outputs()}
                o = outs.get(second)
                if o and o["active"] and o["w"] > 0:
                    log("second display gone, restarting")
                    self.second.proc = None
                    self.layout_screens(panel, second)
                    self.second.start(o["x"], o["y"], o["w"], o["h"])

            if first or second != prev_second or geometry != prev_geometry:
                if not first and geometry != prev_geometry:
                    log(f"panel geometry changed: {prev_geometry} -> {geometry}")

                if first:
                    log(f"panel: {panel}, second display: {second or 'none'}")
                    if not has_projectm():
                        log("projectMSDL not installed - "
                            "running as second display only")

                self.layout_screens(panel, second)

                if not first:
                    if second and not prev_second:
                        log(f"second display connected: {second}")
                    elif not second and prev_second:
                        log(f"second display disconnected: {prev_second}")
                        if (self.vis.is_running()
                                and self.vis.output == prev_second):
                            self.vis.stop()
                            self.set_right_button(True)
                        self.second.stop()

                if second and not self.vis.is_running():
                    # After --auto the output needs a moment to get a mode
                    for _ in range(10):
                        outs = {o["name"]: o for o in get_outputs()}
                        o = outs.get(second)
                        if o and o["active"] and o["w"] > 0:
                            self.second.start(o["x"], o["y"], o["w"], o["h"])
                            break
                        time.sleep(0.5)
                    else:
                        log(f"could not enable second display {second}")

                prev_second = second
                prev_geometry = self.panel_geometry(panel) if panel else None
                first = False

            time.sleep(POLL_INTERVAL)


def main():
    if os.geteuid() != 0:
        print("Run as root:  sudo python3 dual-display.py")
        sys.exit(1)

    log("Dual Display started")
    ctl = Controller()

    def shutdown(signum=None, frame=None):
        """
        Clean up before leaving.
        systemd sends SIGTERM rather than KeyboardInterrupt - without a
        handler the second display browser would be left hanging with
        stale settings.
        """
        log("stopping")
        if ctl.vis.is_running():
            ctl.vis.stop()
            ctl.set_right_button(True)
        ctl.second.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        ctl.loop()
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
