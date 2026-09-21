# Dual Display

Developed for the Coil Sounds audio system by Konstantin Nabutovsky.

A Volumio plugin that brings up a second display.

Volumio drives a single display out of the box — the kiosk opens on the
first output and the second one stays unused. This plugin lays out the
outputs and gives the second display its own browser window with the
Volumio interface.

On x86 that display can also become a MilkDrop visualizer driven by the
audio actually playing. projectMSDL has no ARM builds, so on Raspberry Pi
the plugin works as a second display only — the installer detects this
and skips the visualizer half.

## What it does

The front panel keeps the regular Volumio kiosk and the touchscreen stays
bound to it. The second display, when attached, gets its own window with
the full Volumio interface.

Attaching and detaching is picked up on the fly, nothing needs restarting.

### Visualizer, x86 only

| Action | Result |
|---|---|
| Double tap on the panel | visualizer on / off |
| Single tap | next preset |

With the visualizer on the panel a tap anywhere will do — there is no
interface underneath it.

With it on the second display, while the panel shows the Volumio
interface, only taps inside a set zone count — the left third by default,
where the album art sits. Otherwise every press on the player controls
would skip the picture along. The zone is configurable.

The right mouse button is switched off while the visualizer is on screen
and given back as soon as it goes off. A right click on the visualizer
would hand focus to the Volumio kiosk, which then comes up on top. At any
other time, and on Raspberry Pi altogether, the mouse is left alone.

On a system without projectMSDL the settings page shows only the display
section — there is nothing to set for a visualizer that is not there.

Media keys are left alone: the Volumio kiosk picks them up itself.

## Installation

Over SSH on the Volumio machine:

```
git clone https://github.com/Geser-777/volumio-dual-display.git
cd volumio-dual-display
volumio plugin install
sudo systemctl restart volumio
```

The restart matters when installing over an earlier version: Volumio keeps
the old plugin code in memory until it is restarted.

Reboot afterwards: the output layout takes effect when X starts.

Issues and suggestions: https://github.com/Geser-777/volumio-dual-display/issues

## Picture settings, x86 only

In the Visualizer section of the plugin settings:

| Setting | What it does |
|---|---|
| Change presets automatically | off — a preset stays until you tap; on — they change by themselves |
| Preset duration | seconds per preset when changing automatically |
| Transition time | seconds of blending between presets, 0 for an instant cut |
| Beat sensitivity | how strongly the picture follows the beat, 0 to 2 |
| Frame rate | lower it if the picture stutters or the fan gets loud |

All of it is passed to projectMSDL on the command line; its own
configuration file is left alone.

## How the panel is picked

First by connector name. `DSI`, `DPI`, `LVDS` and `eDP` only ever carry
a built-in panel, and some of their drivers report no physical size at
all, so the name is checked before anything else.

Otherwise by physical size, which `xrandr` reports in millimetres — a
built-in panel is always smaller than a television or a monitor. This
covers HDMI and DVI panels, which look like any other display to the
system.

Output order in the list is no good: it differs from board to board and
does not match the physical connectors.

If the guess is wrong, set the outputs explicitly in the plugin settings —
that always wins. Names come from:

```
sudo XAUTHORITY=$(ls -t /tmp/serverauth.* | head -n1) DISPLAY=:0 xrandr
```

## How the audio works

Volumio sends audio straight to the DAC, so the visualizer cannot hear it
the usual way. But Volumio's own ALSA configuration already has a FIFO at
`/tmp/stream.mp3` carrying a copy of the stream at 44100/16.

```
Volumio → /tmp/stream.mp3 → ALSA loopback → projectMSDL
```

The main audio path is left untouched.

## Presets

Downloaded during installation on x86, not shipped inside the plugin:

- the original Milkdrop preset set, 552 presets —
  `github.com/projectM-visualizer/presets-milkdrop-original`
- the Milkdrop texture pack, needed by many presets —
  `github.com/projectM-visualizer/presets-milkdrop-texture-pack`

Both are pinned to a fixed commit, so every install gets the same set.
They end up in:

```
/data/plugins/user_interface/dual_display/presets
/data/plugins/user_interface/dual_display/textures
```

The search is recursive, so any presets dropped in there are picked up.

Installation needs internet on x86 — projectMSDL is downloaded the same
way. On ARM there is no visualizer and nothing is downloaded.

More sets, if you want to expand:

```
github.com/projectM-visualizer/presets-cream-of-the-crop
github.com/projectM-visualizer/presets-projectm-classic
```

### Credits

The presets are the work of many Milkdrop authors, and the textures come
with the original Milkdrop and from preset packs over the years. Milkdrop
presets were almost never released under any licence. The projectM team,
who maintain these collections, treat them as effectively public domain
and remove a preset from future releases if its author asks. This plugin
does not redistribute them — it fetches them from the projectM
repositories at install time.

Thanks to Ryan Geiss for Milkdrop, to the projectM team, and to every
preset author.

## Translations

The plugin ships with eleven languages:

```
en  English      de  Deutsch      fr  Français
es  Español      it  Italiano     nl  Nederlands
pt  Português    pl  Polski       cs  Čeština
ru  Русский      ua  Українська
```

Volumio has around thirty. For the rest it falls back to English, so
everything works in any case — the settings page is simply in English.

**Corrections and new translations are welcome.** Only English and Russian
were written by a native speaker; the others are a best effort and may
well read awkwardly in places. If something is off, or your language is
missing, send the file to **geser777@gmail.com** and it will go into the
next release.

To make one: copy `i18n/strings_en.json`, translate the values on the
right, keep the keys as they are, and name the file after the language
code — `strings_ja.json`, `strings_tr.json`, `strings_zh.json` and so on.
The codes match the files in `/volumio/app/i18n/`. Volumio picks the file
up by itself and switches with the system language.

## Working alongside Pi Screen Setup

Pi Screen Setup configures the boot parameters — `config.txt`,
`cmdline.txt`, overlays and timings — so that a display comes up at all.
This plugin lays out the outputs in X and places the windows. The two
do not overlap.

Rotation set up by that plugin is left alone: `xrandr --auto` resets
rotation, so it is only used on an output that has no mode yet.

The second display is asked for its preferred mode explicitly. Moving
outputs around sometimes leaves it on a fallback resolution — 1280x1024
in place of 1920x1080, say — and the picture ends up stretched.

The second display is also set to normal rotation. Touch Display rotates
whichever output is primary at the time, and at boot that can still be
the second display — the plugin has not reassigned primary to the panel
yet. The panel itself is left alone: rotating it is up to Touch Display
or Pi Screen Setup.

**Portrait panels are only partly supported.** The plugin was built and
tested with ordinary landscape displays. A portrait panel, DSI especially,
works once its rotation is sorted out, but getting there depends on the
panel and on the other display plugins rather than on this one.

A portrait panel may need both of them set — Pi Screen Setup rotates at
boot, so the splash screen comes up the right way round, and Touch
Display rotates X. Get that working before installing this plugin.

Panel size is watched as well. Rotating a portrait panel swaps its width
and height, and the second display has to move accordingly — it sits at
the panel's width. Rotate the panel through Touch Display or by hand and
the layout is recalculated within a couple of seconds.

## A black border around the picture

The KMS driver on Raspberry Pi sets non-zero margins on an output — this
is underscan, a leftover from analogue television. The picture shrinks and
a black band, usually 48 pixels, is left around it.

The plugin clears the margins every time it lays out the displays.
By hand:

```
sudo XAUTHORITY=$(ls -t /tmp/serverauth.* | head -n1) DISPLAY=:0 \
  xrandr --output HDMI-2 --set "left margin" 0 --set "right margin" 0 \
  --set "top margin" 0 --set "bottom margin" 0
```

Current values:

```
sudo XAUTHORITY=$(ls -t /tmp/serverauth.* | head -n1) DISPLAY=:0 \
  xrandr --prop | grep -A25 "^HDMI-2"
```

## Permissions

The service runs as root: it needs `xrandr`, `xinput` and the input
devices.

The browser runs as whoever owns the X session — root on x86 images,
`volumio` on Raspberry Pi. The X authority file belongs to that user, and
another process would get `Authorization required` and fail to open the
display. The browser profile belongs to the same user for the same reason.

## What the plugin changes outside its own folder

```
/etc/systemd/system/dual-display.service              service
/etc/X11/xorg.conf.d/10-dual-display-monitors.conf    output layout
snd-aloop line in /etc/modules                        ALSA loopback, x86
/data/volumiokiosk-second                             browser profile
```

All of it is removed on uninstall, except projectMSDL itself — it may be
in use on its own. Remove it by hand with `sudo apt remove projectmsdl`.

The plugin does not touch `projectMSDL.properties`: everything it needs
is passed on the command line when the visualizer starts. If you have
configured projectMSDL yourself, your settings stay as they are.

After the plugin is removed the second display goes unused again, and X
picks the primary output by itself — usually the one with the higher
resolution. That is the stock behaviour.

## Diagnostics

```
systemctl status dual-display
journalctl -u dual-display -f
```

Whether audio reaches the loopback — the level bars move while something
is playing:

```
sudo aplay -D plughw:1,0 -f S16_LE -r 44100 -c 2 /tmp/stream.mp3 > /dev/null 2>&1 &
sudo arecord -D plughw:1,1 -f S16_LE -r 44100 -c 2 -V stereo -d 5 /dev/null
sudo pkill aplay
```

If `aplay` complains about `Sample format non available`, the device is
set as `hw:` instead of `plughw:`. Whichever side opens the loopback first
locks the format, and without the `plug` layer the other one cannot adapt.

Capture devices projectMSDL sees:

```
projectMSDL --listAudioDevices
```

The one you want is the far side of the loopback — `Loopback PCM (2)`.

## Licence

MIT, see `LICENSE`. This covers the plugin itself. projectMSDL is LGPL and
is downloaded from its own releases; the presets and textures come from the
projectM repositories, see Credits above.
