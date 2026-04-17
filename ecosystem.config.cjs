module.exports = {
  "apps": [
    {
      "name": "api-server",
      "script": "/workspaces/Toolkit/artifacts/api-server/dist/index.mjs",
      "cwd": "/workspaces/Toolkit",
      "interpreter": "node",
      "interpreter_args": "--enable-source-maps",
      "env": {
        "DATABASE_URL": "postgresql://postgres:postgres@localhost/toolkit",
        "PORT": "8080",
        "NODE_ENV": "production",
        "SUB2API_ADMIN_KEY": "sk-06cf1c8b2ff7a09a1f11d7909a6d7cb7dee97e38793d630f25a3bddf2bf0ec16",
        "SUB2API_API_KEY": "sk-06cf1c8b2ff7a09a1f11d7909a6d7cb7dee97e38793d630f25a3bddf2bf0ec16"
      },
      "restart_delay": 3000,
      "max_restarts": 20,
      "watch": false,
      "autorestart": true
    },
    {
      "name": "frontend",
      "script": "pnpm",
      "args": "--filter @workspace/ai-toolkit run dev",
      "cwd": "/workspaces/Toolkit",
      "interpreter": "none",
      "restart_delay": 5000,
      "max_restarts": 20,
      "watch": false,
      "autorestart": true
    },
    {
      "name": "openai-pool",
      "script": "python3",
      "args": "-m openai_pool_orchestrator",
      "cwd": "/workspaces/Toolkit/artifacts/openai-pool",
      "interpreter": "none",
      "env": {
        "PORT": "8000",
        "PYTHONPATH": "/workspaces/Toolkit/artifacts/openai-pool"
      },
      "restart_delay": 5000,
      "max_restarts": 20,
      "watch": false,
      "autorestart": true
    },
    {
      "name": "fakemail-bridge",
      "script": "/workspaces/Toolkit/artifacts/api-server/fakemail_bridge.py",
      "interpreter": "python3",
      "cwd": "/workspaces/Toolkit",
      "restart_delay": 5000,
      "max_restarts": 10,
      "watch": false,
      "autorestart": true
    },
    {
      "name": "xray",
      "script": "/usr/local/bin/xray",
      "args": "run -c /workspaces/Toolkit/xray.json",
      "interpreter": "none",
      "cwd": "/workspaces/Toolkit",
      "restart_delay": 5000,
      "max_restarts": 50,
      "watch": false,
      "autorestart": true
    },
    {
      "name": "remote-exec",
      "script": "/workspaces/Toolkit/remote-exec.js",
      "cwd": "/workspaces/Toolkit",
      "interpreter": "node",
      "env": {
        "EXEC_PORT": "9999"
      },
      "restart_delay": 5000,
      "max_restarts": 20,
      "watch": false,
      "autorestart": true
    },
    {
      "name": "keepalive",
      "script": "/workspaces/Toolkit/keepalive.sh",
      "interpreter": "bash",
      "restart_delay": 10000,
      "max_restarts": 999,
      "watch": false,
      "autorestart": true
    },
    {
      "name": "xray-watchdog",
      "script": "/workspaces/Toolkit/xray-watchdog.sh",
      "interpreter": "bash",
      "cwd": "/workspaces/Toolkit",
      "restart_delay": 5000,
      "max_restarts": 999,
      "watch": false,
      "autorestart": true
    }
  ]
};
