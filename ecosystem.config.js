// PM2 entry for the BRUXMAP booking page — a standalone project at /home/bruxmap_booking
// with its own venv, logs and .env (nothing shared with other projects on the VPS).
// Start:  cd /home/bruxmap_booking && pm2 start ecosystem.config.js && pm2 save
// The ICS link is read from /home/bruxmap_booking/.env (ICS_URL=...), never committed.
module.exports = {
  apps: [
    {
      name: "bruxmap-booking",
      cwd: "/home/bruxmap_booking",
      script: "/home/bruxmap_booking/venv/bin/uvicorn",
      args: "server:app --host 127.0.0.1 --port 4010 --env-file /home/bruxmap_booking/.env",
      interpreter: "none",
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      watch: false,
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      error_file: "/home/bruxmap_booking/logs/error.log",
      out_file: "/home/bruxmap_booking/logs/out.log",
      merge_logs: true,
    },
  ],
};
