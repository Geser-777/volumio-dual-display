#!/bin/bash
#
# Dual Display plugin installer
#
# The visualizer half is x86 only: projectMSDL has no ARM builds.
# On ARM the plugin installs as a second display only.
#

PLUGIN_DIR=/data/plugins/user_interface/dual_display
SERVICE=dual-display
UNIT=/etc/systemd/system/$SERVICE.service
PROFILE=/data/volumiokiosk-second

ARCH=$(uname -m)

echo "Installing Dual Display"
echo "Architecture: $ARCH"

case "$ARCH" in
    x86_64|amd64)
        WITH_VISUALIZER=yes
        ;;
    *)
        WITH_VISUALIZER=no
        echo "No projectMSDL builds for $ARCH - second display only"
        ;;
esac

# --- dependencies -----------------------------------------------------

sudo apt-get update -qq

sudo apt-get -y install --no-install-recommends \
    x11-xserver-utils xinput python3

# Chromium ships with Volumio images - it runs the regular kiosk.
# Install only if somehow missing.
if ! command -v chromium > /dev/null && ! command -v chromium-browser > /dev/null; then
    sudo apt-get -y install --no-install-recommends chromium
fi

if [ "$WITH_VISUALIZER" = "yes" ]; then
    sudo apt-get -y install --no-install-recommends \
        xdotool alsa-utils curl wget
fi

# --- projectMSDL, x86 only -------------------------------------------

if [ "$WITH_VISUALIZER" = "yes" ] && ! command -v projectMSDL > /dev/null; then
    echo "Downloading projectMSDL"

    # Builds are published as pre-releases, so /releases/latest returns
    # nothing. The main repository has had no releases for a while,
    # current ones live in the kblaschke fork.
    REPOS="kblaschke/frontend-sdl-cpp projectM-visualizer/frontend-sdl-cpp"

    URL=""
    for repo in $REPOS; do
        URL=$(curl -s "https://api.github.com/repos/$repo/releases" \
              | grep "browser_download_url" \
              | grep "\.deb" \
              | grep -iE "$ARCH|amd64" \
              | head -n1 \
              | cut -d '"' -f 4)
        [ -n "$URL" ] && break
    done

    if [ -n "$URL" ]; then
        wget -q -O /tmp/projectmsdl.deb "$URL"
        sudo dpkg -i /tmp/projectmsdl.deb > /dev/null 2>&1 || sudo apt-get install -y -f
        rm -f /tmp/projectmsdl.deb
    else
        echo "Could not find a projectMSDL package for $ARCH"
        WITH_VISUALIZER=no
    fi
fi

# --- presets and textures, x86 only ----------------------------------

# Downloaded rather than shipped inside the plugin. Milkdrop presets were
# almost never released under any licence; the projectM team treats them
# as effectively public domain and removes a preset if its author objects.
# Fetching them from the projectM repositories keeps us from redistributing
# them ourselves. Pinned to a commit so every install gets the same set.
PRESETS_REPO=projectM-visualizer/presets-milkdrop-original
PRESETS_COMMIT=e03b83e3338d8f1ed6cbcf908c719f249ef24288
TEXTURES_REPO=projectM-visualizer/presets-milkdrop-texture-pack
TEXTURES_COMMIT=6368812f27bc747b517218fbf89d21d59afce4d9

fetch_repo() {
    # $1 repo, $2 commit, $3 destination
    local tmp
    tmp=$(mktemp -d)
    if wget -q -O "$tmp/a.tgz" \
        "https://codeload.github.com/$1/tar.gz/$2" \
        && tar xzf "$tmp/a.tgz" -C "$tmp" --strip-components=1 \
            --exclude='README.md' 2>/dev/null; then
        rm -f "$tmp/a.tgz"
        sudo rm -rf "$3"
        sudo mkdir -p "$3"
        sudo cp -r "$tmp"/. "$3"/
        rm -rf "$tmp"
        return 0
    fi
    rm -rf "$tmp"
    return 1
}

if [ "$WITH_VISUALIZER" = "yes" ]; then
    echo "Downloading presets"
    if fetch_repo "$PRESETS_REPO" "$PRESETS_COMMIT" "$PLUGIN_DIR/presets"; then
        echo "Presets: $(find "$PLUGIN_DIR/presets" -name '*.milk' | wc -l)"
    else
        echo "Could not download presets - the visualizer will show nothing"
    fi

    echo "Downloading textures"
    if fetch_repo "$TEXTURES_REPO" "$TEXTURES_COMMIT" "$PLUGIN_DIR/textures.tmp"; then
        # The texture pack keeps its files one level down, in textures/
        sudo rm -rf "$PLUGIN_DIR/textures"
        if [ -d "$PLUGIN_DIR/textures.tmp/textures" ]; then
            sudo mv "$PLUGIN_DIR/textures.tmp/textures" "$PLUGIN_DIR/textures"
        else
            sudo mv "$PLUGIN_DIR/textures.tmp" "$PLUGIN_DIR/textures"
        fi
        sudo rm -rf "$PLUGIN_DIR/textures.tmp"
        echo "Textures: $(find "$PLUGIN_DIR/textures" -type f | wc -l)"
    else
        echo "Could not download textures - many presets will look wrong"
    fi
fi

# --- ALSA loopback, visualizer only ----------------------------------

if [ "$WITH_VISUALIZER" = "yes" ]; then
    echo "Setting up ALSA loopback"

    sudo modprobe snd-aloop 2>/dev/null
    if ! grep -q "^snd-aloop" /etc/modules 2>/dev/null; then
        echo "snd-aloop" | sudo tee -a /etc/modules > /dev/null
    fi
fi

# --- display layout ---------------------------------------------------

XAUTH=$(ls -t /tmp/serverauth.* 2>/dev/null | head -n1)

if [ -n "$XAUTH" ]; then
    XR=$(sudo XAUTHORITY="$XAUTH" DISPLAY=:0 xrandr 2>/dev/null)
    OUTPUTS=$(echo "$XR" | grep " connected" | awk '{print $1}')

    PRIMARY=""
    SECONDARY=""

    # Connectors that only ever carry a built-in panel. Some of their
    # drivers report no physical size at all, so the name has to be
    # checked before anything else.
    for out in $OUTPUTS; do
        case "$out" in
            DSI*|dsi*|DPI*|dpi*|LVDS*|lvds*|eDP*|EDP*|edp*)
                PRIMARY=$out
                break
                ;;
        esac
    done

    # Otherwise go by physical size, which xrandr reports in millimetres:
    # a built-in panel is always smaller than a television or a monitor.
    # Output order in the list does not match the physical connectors
    # and differs from board to board.
    BEST_AREA=999999999

    [ -n "$PRIMARY" ] && OUTPUTS_SCAN="" || OUTPUTS_SCAN="$OUTPUTS"

    for out in $OUTPUTS_SCAN; do
        DIMS=$(echo "$XR" | grep "^$out connected" \
               | grep -o '[0-9]\+mm x [0-9]\+mm' | head -n1)
        W=$(echo "$DIMS" | awk '{print $1}' | tr -d 'm')
        H=$(echo "$DIMS" | awk '{print $4}' | tr -d 'm')

        if [ -n "$W" ] && [ -n "$H" ] && [ "$W" -gt 0 ] && [ "$H" -gt 0 ]; then
            AREA=$((W * H))
        else
            # No size reported - treat as large so it is not picked as panel
            AREA=999999998
        fi

        if [ "$AREA" -lt "$BEST_AREA" ]; then
            BEST_AREA=$AREA
            PRIMARY=$out
        fi
    done

    for out in $OUTPUTS; do
        if [ "$out" != "$PRIMARY" ]; then
            SECONDARY=$out
            break
        fi
    done

    if [ -n "$PRIMARY" ]; then
        echo "Front panel: $PRIMARY"
        [ -n "$SECONDARY" ] && echo "Second display: $SECONDARY"

        sudo mkdir -p /etc/X11/xorg.conf.d
        {
            echo "# Written by the Dual Display plugin."
            echo "# Keeps the front panel at the origin and primary,"
            echo "# so the Volumio kiosk opens there."
            echo
            echo "Section \"Monitor\""
            echo "    Identifier \"$PRIMARY\""
            echo "    Option \"Primary\" \"true\""
            echo "    Option \"Position\" \"0 0\""
            echo "EndSection"
            if [ -n "$SECONDARY" ]; then
                echo
                echo "Section \"Monitor\""
                echo "    Identifier \"$SECONDARY\""
                echo "    Option \"RightOf\" \"$PRIMARY\""
                echo "EndSection"
            fi
        } | sudo tee /etc/X11/xorg.conf.d/10-dual-display-monitors.conf > /dev/null
    fi
fi

# --- browser profile --------------------------------------------------

# The browser runs as whoever owns the X session, and the profile has to
# belong to that user. Wipe it on install so nothing is left over from
# a previous run under a different user.
sudo rm -rf "$PROFILE"
sudo mkdir -p "$PROFILE"

if [ -n "$XAUTH" ]; then
    XUSER=$(stat -c '%U' "$XAUTH" 2>/dev/null)
fi
[ -z "$XUSER" ] && XUSER=volumio

sudo chown -R "$XUSER:$XUSER" "$PROFILE"

# --- service ----------------------------------------------------------

echo "Setting up service"

sudo tee "$UNIT" > /dev/null <<UNITEOF
[Unit]
Description=Dual Display for Volumio
After=multi-user.target

[Service]
ExecStart=/usr/bin/python3 $PLUGIN_DIR/scripts/dual-display.py
Restart=always
RestartSec=10
StartLimitBurst=0
User=root

[Install]
WantedBy=multi-user.target
UNITEOF

sudo systemctl daemon-reload

# The plugin starts and stops the service, not the system
sudo systemctl disable "$SERVICE" > /dev/null 2>&1

sudo chmod 755 "$PLUGIN_DIR/scripts/dual-display.py"
sudo chown -R volumio:volumio "$PLUGIN_DIR"

echo "plugininstallend"
