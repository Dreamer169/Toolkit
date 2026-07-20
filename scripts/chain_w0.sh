#!/bin/bash
export WORKER_ID=0
export CHROME_LIMIT=2
export STARTUP_DELAY=0
export N_WORKERS=2
exec python3 /data/Toolkit/scripts/unitool_chain_v3.py
