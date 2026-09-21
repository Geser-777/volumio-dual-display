#!/bin/bash
#
# Dual Display plugin uninstaller
#

SERVICE=dual-display
PROFILE=/data/volumiokiosk-second

echo "Removing Dual Display"

sudo systemctl stop "$SERVICE" 2>/dev/null
sudo systemctl disable "$SERVICE" 2>/dev/null
sudo rm -f "/etc/systemd/system/$SERVICE.service"
sudo systemctl daemon-reload

sudo pkill -x projectMSDL 2>/dev/null
sudo pkill -x aplay 2>/dev/null
sudo pkill -f "user-data-dir=$PROFILE" 2>/dev/null

# Display layout the plugin wrote
sudo rm -f /etc/X11/xorg.conf.d/10-dual-display-monitors.conf

# Browser profile of the second display
sudo rm -rf "$PROFILE"

# ALSA loopback from boot
sudo sed -i '/^snd-aloop$/d' /etc/modules 2>/dev/null

# projectMSDL is left in place: it may be used on its own.
# Remove by hand with:  sudo apt remove projectmsdl

echo "pluginuninstallend"
