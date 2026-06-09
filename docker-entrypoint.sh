#!/bin/sh
set -eu

ENV_FILE=/etc/frame.env
CRON_JOB=/usr/local/bin/run_frame_job.sh

python3 - <<'PY'
import os
import shlex

output_path = "/etc/frame.env"
keep_prefixes = (
    "PHOTOFRAME_",
    "CITY",
    "GOOGLE_CALENDAR_",
    "TMDB_",
    "SCREEN_SCHEDULE_",
    "TZ",
    "WEATHER_DEBUG_LOGS",
    "BEAR_DEBUG_LOGS",
    "SIMULATE_LONG_EVENTS",
)

with open(output_path, "w", encoding="utf-8") as handle:
    for key, value in sorted(os.environ.items()):
        if not key.startswith(keep_prefixes):
            continue
        handle.write(f"export {key}={shlex.quote(value)}\n")
PY

chmod 0644 "$ENV_FILE"

if [ -n "${TZ:-}" ] && [ -e "/usr/share/zoneinfo/${TZ}" ]; then
    ln -snf "/usr/share/zoneinfo/${TZ}" /etc/localtime
    echo "$TZ" > /etc/timezone
fi

cat > "$CRON_JOB" <<'SH'
#!/bin/sh
set -eu

if [ -f /etc/frame.env ]; then
    . /etc/frame.env
fi

exec su -p -s /bin/sh appuser -c '/usr/local/bin/python3 /app/main.py'
SH
chmod 0755 "$CRON_JOB"

exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
