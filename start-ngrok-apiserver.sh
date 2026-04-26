#!/bin/bash
exec /usr/local/bin/ngrok http 8081 --config=/root/.config/ngrok-sub2api/ngrok.yml --domain=strive-phoney-vocalize.ngrok-free.dev --log=stdout
