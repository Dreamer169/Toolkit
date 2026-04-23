#!/bin/bash
exec /usr/bin/Xvfb :99 -screen 0 1920x1080x24 -ac +extension RANDR +extension GLX +render -noreset
