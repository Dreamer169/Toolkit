#!/bin/bash
cd /root/Toolkit/artifacts/ai-toolkit
exec ./node_modules/.bin/vite --config vite.config.ts --host 0.0.0.0 --port 3000
