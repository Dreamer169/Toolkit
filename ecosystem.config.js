module.exports = {
  apps: [
    {
      name: "unitool_chain_v3",
      script: "/data/Toolkit/scripts/unitool_chain_v3.py",
      interpreter: "python3",
      cwd: "/data/Toolkit/scripts",
      autorestart: true,
      watch: false,
      env: {
        DISPLAY: ":102",
        PATH: process.env.PATH
      }
    },
    {
      name: "unitool_verify_rescue",
      script: "/data/Toolkit/scripts/unitool_verify_rescue.py",
      interpreter: "python3",
      cwd: "/data/Toolkit/scripts",
      autorestart: true,
      watch: false,
      env: {
        DISPLAY: ":102",
        PATH: process.env.PATH
      }
    }
  ]
}
