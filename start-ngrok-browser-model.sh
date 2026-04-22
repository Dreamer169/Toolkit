#!/bin/bash
exec /usr/local/bin/ngrok http 8092 --config=/root/.config/ngrok-browser-model/ngrok.yml --request-header-add "ngrok-skip-browser-warning:true" --log=stdout
