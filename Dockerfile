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

# Cron job: execute the scheduled action every 15 minutes.
# Send output to container stdout/stderr so Coolify can collect logs.
RUN echo "*/15 * * * * appuser python /app/main.py >> /proc/1/fd/1 2>> /proc/1/fd/2" > /etc/crontab \
    && chmod 0644 /etc/crontab

# Useful debug command if needed when troubleshooting container lifecycle:
# tail -f /dev/null

CMD ["cron", "-f"]
