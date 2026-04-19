#!/bin/bash
exec /usr/local/bin/ngrok http 9090 --config=/root/.config/ngrok-gateway/ngrok.yml --domain=fantasize-outtakes-backpedal.ngrok-free.dev --log=stdout
