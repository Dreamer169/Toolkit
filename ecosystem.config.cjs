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
        "LOCAL_GATEWAY_BASE_URL": "https://strive-phoney-vocalize.ngrok-free.dev/api/gateway",
        "REPLIT_SUBNODES": "https://gh-cli-direct--elizabetha96.replit.app/api/gateway",
        "CONNECT_PROXY_TOKEN": "vps_direct_proxy_2024",
        "FORCE_REGISTER_PORTS": "",
        "NO_WARP_OVERRIDE": "0"
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
      "name": "xvfb",
      "script": "/root/Toolkit/start-xvfb.sh",
      "interpreter": "bash",
      "cwd": "/root/Toolkit",
      "restart_delay": 3000,
      "max_restarts": 50,
      "watch": false,
      "autorestart": true,
      "kill_timeout": 3000
    },
    {
      "name": "ngrok",
      "script": "/usr/local/bin/ngrok",
      "args": "http 3000 --domain=recycling-tragedy-projector.ngrok-free.dev --request-header-add ngrok-skip-browser-warning:true --log=stdout",
      "interpreter": "none",
      "cwd": "/root/Toolkit",
      "restart_delay": 5000,
      "max_restarts": 20,
      "watch": false,
      "autorestart": true
    },
    {
      "name": "ngrok-gateway",
      "script": "/root/Toolkit/start-ngrok-gateway.sh",
      "interpreter": "bash",
      "cwd": "/root/Toolkit",
      "restart_delay": 5000,
      "max_restarts": 20,
      "watch": false,
      "autorestart": true
    },
    {
      "name": "sub2api",
      "script": "/opt/sub2api/sub2api",
      "interpreter": "none",
      "cwd": "/opt/sub2api",
      "restart_delay": 5000,
      "max_restarts": 20,
      "watch": false,
      "autorestart": true
    },
    {
      "name": "http-socks5-bridge",
      "script": "/root/Toolkit/http_ws_bridge.py",
      "interpreter": "python3",
      "cwd": "/root/Toolkit",
      "env": {
        "SOCKS_PORT": "1090",
        "BASE_URLS": "https://gh-cli-direct--elizabetha96.replit.app",
        "TUNNEL_TOKEN": "1NnCcQJcNgwlTDPEnDIkWEKzWIdmZ/4+BmsOp1/jLP6ojCWsv8+xTwcLj34Mu2viWy0q5SEoDP0q2qE5xHaRRg==",
        "WS_PATH": "/api/stream/ws"
      },
      "restart_delay": 5000,
      "max_restarts": 30,
      "watch": false,
      "autorestart": true
    },
    {
      "name": "ws-tunnel-bridge",
      "script": "/root/Toolkit/ws_tunnel_bridge.py",
      "interpreter": "python3",
      "cwd": "/root/Toolkit",
      "env": {
        "SOCKS_PORT": "1091",
        "WS_SERVERS": "wss://gh-cli-install--jessicaphilli10.replit.app/api/stream/ws,wss://gh-cli-install--bandersonndz.replit.app/api/stream/ws,wss://gh-cli-direct--elizabetha96.replit.app/api/stream/ws",
        "WS_TOKEN": "1NnCcQJcNgwlTDPEnDIkWEKzWIdmZ/4+BmsOp1/jLP6ojCWsv8+xTwcLj34Mu2viWy0q5SEoDP0q2qE5xHaRRg=="
      },
      "restart_delay": 5000,
      "max_restarts": 30,
      "watch": false,
      "autorestart": true
    },
    {
      "name": "ws-socks5-bridge",
      "script": "/root/Toolkit/ws_socks5_bridge.py",
      "interpreter": "python3",
      "cwd": "/root/Toolkit",
      "env": {
        "BRIDGE_PORT": "1089",
        "WS_URL": "wss://gh-cli-install--jessicaphilli10.replit.app/api/stream/ws",
        "WS_TOKEN": "1NnCcQJcNgwlTDPEnDIkWEKzWIdmZ/4+BmsOp1/jLP6ojCWsv8+xTwcLj34Mu2viWy0q5SEoDP0q2qE5xHaRRg=="
      },
      "restart_delay": 5000,
      "max_restarts": 30,
      "watch": false,
      "autorestart": true
    },
    {
      "name": "subnode-keepalive",
      "script": "/root/Toolkit/subnode_keepalive.sh",
      "interpreter": "bash",
      "cwd": "/root/Toolkit",
      "restart_delay": 10000,
      "max_restarts": 999,
      "watch": false,
      "autorestart": true
    },
    {
      "name": "ngrok-apiserver",
      "script": "/root/Toolkit/start-ngrok-apiserver.sh",
      "interpreter": "bash",
      "cwd": "/root/Toolkit",
      "restart_delay": 5000,
      "max_restarts": 20,
      "watch": false,
      "autorestart": true
    },
    {
      "name": "http-poll-bridge",
      "script": "/root/Toolkit/http_poll_bridge.py",
      "interpreter": "python3",
      "cwd": "/root/Toolkit",
      "restart_delay": 5000,
      "max_restarts": 50,
      "watch": false,
      "autorestart": true,
      "env": {
        "SOCKS_PORT": "1092",
        "STREAM_TOKEN": "123456",
        "GATEWAY_API": "http://localhost:8080/api",
        "SUBNODE_URLS": "https://gh-cli-direct--elizabetha96.replit.app",
        "REFRESH_SECS": "30",
        "PYTHONUNBUFFERED": "1"
      }
    }
,
    {
      "name": "http-poll-bridge-2",
      "script": "/root/Toolkit/http_poll_bridge.py",
      "interpreter": "python3",
      "cwd": "/root/Toolkit",
      "restart_delay": 5000,
      "max_restarts": 50,
      "watch": false,
      "autorestart": true,
      "env": {
        "SOCKS_PORT": "1093",
        "STREAM_TOKEN": "123456",
        "GATEWAY_API": "http://localhost:8080/api",
        "SUBNODE_URLS": "https://gh-cli-direct--elizabetha96.replit.app",
        "REFRESH_SECS": "30",
        "PYTHONUNBUFFERED": "1"
      }
    },
    {
      "name": "http-poll-bridge-3",
      "script": "/root/Toolkit/http_poll_bridge.py",
      "interpreter": "python3",
      "cwd": "/root/Toolkit",
      "restart_delay": 5000,
      "max_restarts": 50,
      "watch": false,
      "autorestart": true,
      "env": {
        "SOCKS_PORT": "1094",
        "STREAM_TOKEN": "123456",
        "GATEWAY_API": "http://localhost:8080/api",
        "SUBNODE_URLS": "https://gh-cli-direct--elizabetha96.replit.app",
        "REFRESH_SECS": "30",
        "PYTHONUNBUFFERED": "1"
      }
    },
    {
      "name": "http-poll-bridge-4",
      "script": "/root/Toolkit/http_poll_bridge.py",
      "interpreter": "python3",
      "cwd": "/root/Toolkit",
      "restart_delay": 5000,
      "max_restarts": 50,
      "watch": false,
      "autorestart": true,
      "env": {
        "SOCKS_PORT": "1095",
        "STREAM_TOKEN": "123456",
        "GATEWAY_API": "http://localhost:8080/api",
        "SUBNODE_URLS": "https://gh-cli-direct--elizabetha96.replit.app",
        "REFRESH_SECS": "30",
        "PYTHONUNBUFFERED": "1"
      }
    },
    {
      "name": "http-connect-proxy",
      "script": "/root/Toolkit/artifacts/api-server/http_connect_proxy.py",
      "interpreter": "python3",
      "cwd": "/root/Toolkit",
      "env": {
        "CONNECT_PROXY_PORT": "8091",
        "CONNECT_PROXY_TOKEN": "vps_direct_proxy_2024",
        "FORCE_REGISTER_PORTS": "",
        "NO_WARP_OVERRIDE": "0",
        "PYTHONUNBUFFERED": "1"
      },
      "restart_delay": 3000,
      "max_restarts": 50,
      "watch": false,
      "autorestart": true
    },
    {
      "name": "browser-model",
      "script": "/root/Toolkit/start-browser-model.sh",
      "interpreter": "bash",
      "cwd": "/root/Toolkit",
      "restart_delay": 5000,
      "max_restarts": 30,
      "watch": false,
      "autorestart": true
    }
  ]
};