import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn
from openai_pool_orchestrator.server import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 18421))
    print(f"🚀 OpenAI Pool Orchestrator 启动: http://0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
