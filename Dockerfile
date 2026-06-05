FROM python:3.12-slim

# Désactive le buffer de sortie pour voir les logs en temps réel
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Installe les dépendances système (y compris cron)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        cron \
        libcairo2 \
        libpango-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Installe les dépendances Python
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copie le code source
COPY . .

# Crée un utilisateur dédié
RUN useradd -m appuser && chown -R appuser:appuser /app

# --- Configuration de cron ---
# 1. Crée un fichier de log pour cron (optionnel, mais utile pour le débogage)
RUN touch /var/log/cron.log && chmod 666 /var/log/cron.log

# 2. Configure la tâche cron pour rediriger les logs vers stdout/stderr
#    Utilise `>> /proc/1/fd/1` pour stdout et `2>> /proc/1/fd/2` pour stderr
#    (Le PID 1 dans Docker est le processus principal, ici `cron`).
RUN echo "*/5 * * * * appuser python /app/main.py >> /proc/1/fd/1 2>> /proc/1/fd/2" > /etc/crontab \
    && chmod 0644 /etc/crontab

# 3. Active cron en arrière-plan et garde le container actif avec `tail -f /dev/null`
#    (Si `cron` plante, le container reste actif grâce à `tail -f /dev/null`)
CMD ["sh", "-c", "service cron start && tail -f /dev/null"]