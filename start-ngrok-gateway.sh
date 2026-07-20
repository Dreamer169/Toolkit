#!/bin/bash
# ngrok gateway tunnel (HTTP/HTTPS passthrough)
exec ngrok http 80 --domain="" 2>&1 || sleep 60
