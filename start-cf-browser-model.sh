#!/bin/bash
exec /usr/local/bin/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8092 --metrics 127.0.0.1:23092
