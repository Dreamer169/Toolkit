#!/bin/bash
exec /usr/local/bin/ngrok http 8080 --config=/root/.config/ngrok-sub2api/ngrok.yml --domain=strive-phoney-vocalize.ngrok-free.dev --log=stdout
