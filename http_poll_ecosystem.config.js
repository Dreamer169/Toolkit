module.exports = {
  apps: [
    { name: "http-poll-bridge",   script: "/root/Toolkit/http_poll_bridge.py", interpreter: "python3", cwd: "/root/Toolkit", restart_delay: 3000, max_restarts: 999, env: { PYTHONUNBUFFERED: "1", REFRESH_SECS: "30", SUBNODE_URLS: "https://gh-cli-direct--elizabetha96.replit.app", GATEWAY_API: "http://localhost:8081/api", STREAM_TOKEN: "123456", SOCKS_PORT: "1092" } },
    { name: "http-poll-bridge-2", script: "/root/Toolkit/http_poll_bridge.py", interpreter: "python3", cwd: "/root/Toolkit", restart_delay: 3000, max_restarts: 999, env: { PYTHONUNBUFFERED: "1", REFRESH_SECS: "30", SUBNODE_URLS: "https://gh-cli-direct--elizabetha96.replit.app", GATEWAY_API: "http://localhost:8081/api", STREAM_TOKEN: "123456", SOCKS_PORT: "1093" } },
    { name: "http-poll-bridge-3", script: "/root/Toolkit/http_poll_bridge.py", interpreter: "python3", cwd: "/root/Toolkit", restart_delay: 3000, max_restarts: 999, env: { PYTHONUNBUFFERED: "1", REFRESH_SECS: "30", SUBNODE_URLS: "https://gh-cli-direct--elizabetha96.replit.app", GATEWAY_API: "http://localhost:8081/api", STREAM_TOKEN: "123456", SOCKS_PORT: "1094" } },
    { name: "http-poll-bridge-4", script: "/root/Toolkit/http_poll_bridge.py", interpreter: "python3", cwd: "/root/Toolkit", restart_delay: 3000, max_restarts: 999, env: { PYTHONUNBUFFERED: "1", REFRESH_SECS: "30", SUBNODE_URLS: "https://gh-cli-direct--elizabetha96.replit.app", GATEWAY_API: "http://localhost:8081/api", STREAM_TOKEN: "123456", SOCKS_PORT: "1095" } }
  ]
}
