module.exports = {
  apps: [{
    name: "autoprovision",
    script: "/root/Toolkit/scripts/obvious_autoprovision.py",
    interpreter: "python3",
    cwd: "/root/Toolkit/scripts",
    watch: false,
    autorestart: true,
    max_restarts: 20,
    min_uptime: "30s",
    args: "--watch --min-active 10 --check-interval 600",
    env: {
      DISPLAY: ":99",
      SB_ACC_DIR: "/root/obvious-accounts",
      SB_MIN_POOL: "10",
    }
  }]
};
