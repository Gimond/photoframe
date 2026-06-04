FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System libs required by CairoSVG rendering pipeline.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        cron \
        libcairo2 \
        libpango-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m appuser && chown -R appuser:appuser /app

# Cron job: execute the scheduled action every 5 minutes.
# Use /etc/crontab format with an explicit user.
RUN echo "*/15 * * * * appuser python /app/main.py >> /var/log/cron.log 2>&1" > /etc/crontab \
    && chmod 0644 /etc/crontab \
    && touch /var/log/cron.log

# Useful debug command if needed when troubleshooting container lifecycle:
# tail -f /dev/null

CMD ["cron", "-f"]
