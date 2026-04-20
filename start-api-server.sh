#!/bin/bash
set -a
source /root/Toolkit/.env.local 2>/dev/null
set +a
node --enable-source-maps /root/Toolkit/artifacts/api-server/dist/index.mjs
