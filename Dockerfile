FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Installe les dépendances
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        cron \
        supervisor \
        libcairo2 \
        libpango-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m appuser && chown -R appuser:appuser /app

# Configure cron
# Run as root for stdout/stderr redirection, then drop to appuser for Python execution.
RUN echo "*/1 * * * * root su -s /bin/sh appuser -c '/usr/local/bin/python3 /app/main.py' >> /proc/1/fd/1 2>> /proc/1/fd/2" > /etc/crontab \
    && chmod 0644 /etc/crontab

# Configure supervisord
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]