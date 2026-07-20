#!/bin/bash
# ngrok api-server tunnel
exec ngrok http 8081 2>&1 || sleep 60
