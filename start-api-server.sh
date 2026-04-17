#!/bin/bash
export $(cat /root/Toolkit/.env.local 2>/dev/null | xargs)
exec node --enable-source-maps /root/Toolkit/artifacts/api-server/dist/index.mjs
