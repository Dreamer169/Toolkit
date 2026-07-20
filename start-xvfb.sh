#!/bin/bash
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null
exec /usr/bin/Xvfb :99 -screen 0 1920x1080x24 -ac +extension RANDR +extension GLX +render -noreset
