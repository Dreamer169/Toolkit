module.exports = {
  "apps": [
    {
      "name": "api-server",
      "script": "/root/Toolkit/artifacts/api-server/dist/index.mjs",
      "cwd": "/root/Toolkit",
      "interpreter": "node",
      "interpreter_args": "--enable-source-maps",
      "env": {
        "DATABASE_URL": "postgresql://postgres:postgres@localhost/toolkit",
        "PORT": "8080",
        "REMOTE_GATEWAY_BASE_URL": "http://localhost:9090",
        "SUB2API_ADMIN_BASE_URL": "http://localhost:9090",
        "SUB2API_ADMIN_EMAIL": "admin@proxy.local",
        "SUB2API_ADMIN_PASSWORD": "Proxy2024",
        "NODE_ENV": "production",
        "SUB2API_ADMIN_KEY": "sk-06cf1c8b2ff7a09a1f11d7909a6d7cb7dee97e38793d630f25a3bddf2bf0ec16",
        "SUB2API_API_KEY": "sk-06cf1c8b2ff7a09a1f11d7909a6d7cb7dee97e38793d630f25a3bddf2bf0ec16",
        "LOCAL_GATEWAY_BASE_URL": "https://f38ac22e-4245-491d-846c-65910b06bf40-00-dj9hp8j85c9.spock.replit.dev/api/gateway",
        "REPLIT_SUBNODES": "https://f38ac22e-4245-491d-846c-65910b06bf40-00-dj9hp8j85c9.spock.replit.dev/api/gateway"
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
      "cwd": "/root/Toolkit",
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
      "cwd": "/root/Toolkit/artifacts/openai-pool",
      "interpreter": "none",
      "env": {
        "PORT": "8000",
        "PYTHONPATH": "/root/Toolkit/artifacts/openai-pool"
      },
      "restart_delay": 5000,
      "max_restarts": 20,
      "watch": false,
      "autorestart": true
    },
    {
      "name": "fakemail-bridge",
      "script": "/root/Toolkit/artifacts/api-server/fakemail_bridge.py",
      "interpreter": "python3",
      "cwd": "/root/Toolkit",
      "restart_delay": 5000,
      "max_restarts": 10,
      "watch": false,
      "autorestart": true
    },
    {
      "name": "xray",
      "script": "/usr/local/bin/xray",
      "args": "run -c /root/Toolkit/xray.json",
      "interpreter": "none",
      "cwd": "/root/Toolkit",
      "restart_delay": 5000,
      "max_restarts": 50,
      "watch": false,
      "autorestart": true
    },
    {
      "name": "remote-exec",
      "script": "/root/Toolkit/remote-exec.js",
      "cwd": "/root/Toolkit",
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
      "script": "/root/Toolkit/keepalive.sh",
      "interpreter": "bash",
      "restart_delay": 10000,
      "max_restarts": 999,
      "watch": false,
      "autorestart": true
    },
    {
      "name": "xray-watchdog",
      "script": "/root/Toolkit/xray-watchdog.sh",
      "interpreter": "bash",
      "cwd": "/root/Toolkit",
      "restart_delay": 5000,
      "max_restarts": 999,
      "watch": false,
      "autorestart": true
    },
    {
      "name": "ngrok",
      "script": "/usr/local/bin/ngrok",
      "args": "http 3000 --domain=tried-habitant-kindly.ngrok-free.dev --log=stdout",
      "interpreter": "none",
      "cwd": "/root/Toolkit",
      "restart_delay": 5000,
      "max_restarts": 20,
      "watch": false,
      "autorestart": true
    },
  ]
};
