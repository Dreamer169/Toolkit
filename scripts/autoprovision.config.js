module.exports = {
  apps: [{
    name: "autoprovision",
    script: "/root/Toolkit/scripts/obvious_autoprovision.py",
    interpreter: "python3",
    cwd: "/root/Toolkit/scripts",
    watch: false,          // 明确禁用 PM2 文件监控
    autorestart: true,     // 进程崩溃时重启（但不因文件变动重启）
    max_restarts: 20,
    min_uptime: "30s",     // 30s 内退出算不稳定，避免 PM2 crash-loop
    args: "--watch --min-active 10 --check-interval 600",
    env: {
      DISPLAY: ":99",
      SB_ACC_DIR: "/root/obvious-accounts",
      SB_MIN_POOL: "10",
    }
  }]
};
