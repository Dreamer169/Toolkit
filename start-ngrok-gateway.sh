#!/bin/bash
exec /usr/local/bin/ngrok http 8080 --config=/root/.config/ngrok-gateway/ngrok.yml --domain=fantasize-outtakes-backpedal.ngrok-free.dev --log=stdout
