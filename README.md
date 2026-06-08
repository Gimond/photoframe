# Frame

Génération et envoi d'écrans pour un cadre photo (e-ink), avec exécution planifiée toutes les minutes via cron dans Docker.

## Fonctionnalités

- Génération d'écrans Python avec Pillow.
- Météo via Open-Meteo (pas de clé API nécessaire).
- Intégration calendrier ICS (Google Calendar ou autre flux ICS).
- Écran TMDB depuis une liste utilisateur.
- Sélection d'écran automatique via planning JSON.
- Envoi de l'image vers l'API du cadre photo.

## Écrans disponibles

- meteo_calendar
- meteo_calendar_glance
- meteo_calendar_bear
- meteo_calendar_bear_today
- meteo_calendar_bear_tomorrow
- tmdb_random_list

## Prérequis

- Python 3.12+
- Docker Desktop (si exécution conteneurisée)

## Configuration

1. Copier `.env.example` vers `.env`.
2. Renseigner les variables nécessaires.

Variables principales:

- PHOTOFRAME_API_URLS: une ou plusieurs URL API de base (terminées par /api/) séparées par des virgules.
- CITY: ville pour la météo, ex: `Massingy,fr`.
- GOOGLE_CALENDAR_ICS_URLS: un ou plusieurs flux ICS séparés par des virgules.
- TMDB_API_KEY: clé API TMDB (pour l'écran TMDB).
- TMDB_LIST_ID: ID de liste TMDB.
- SCREEN_SCHEDULE_FILE: fichier de planning (par défaut `schedule.json`).
- SCREEN_SCHEDULE_STATE_FILE: état des règles déjà exécutées.
- SCREEN_SCHEDULE_TIMEZONE: timezone utilisée pour comparer les règles horaires (ex: `Europe/Paris`).
- TZ: variable timezone standard, utilisée si `SCREEN_SCHEDULE_TIMEZONE` est vide.

## Exécution locale (sans Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Générer selon le planning et enregistrer localement:

```bash
python main.py --save-local
```

Forcer un écran précis:

```bash
python main.py --screen meteo_calendar --save-local
python main.py --screen meteo_calendar_glance --save-local
python main.py --screen meteo_calendar_bear --save-local
python main.py --screen meteo_calendar_bear_today --save-local
python main.py --screen meteo_calendar_bear_tomorrow --save-local
python main.py --screen tmdb_random_list --save-local
```

Sortie personnalisée:

```bash
python main.py --screen meteo_calendar --save-local --output ./screen.png
```

## Exécution Docker

Build:

```bash
docker build --no-cache -t frame .
```

Run:

```bash
docker rm -f condescending_rubin 2>/dev/null || true
docker run -d --name condescending_rubin frame
```

Logs:

```bash
docker logs -f --tail 200 condescending_rubin
```

Le conteneur:

- lance supervisord,
- démarre cron en foreground,
- exécute `main.py` chaque minute,
- envoie les logs Python sur stdout/stderr du conteneur.

## Planning automatique

Le fichier `schedule.json` définit les fenetres d'affichage selon jour/plage horaire.

Exemple:

```json
{
  "default": "meteo_calendar",
  "rules": [
    {
      "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
      "from": "07:00",
      "to": "09:00",
      "screen": "meteo_calendar"
    },
    {
      "days": ["sat"],
      "from": "16:30",
      "to": "19:00",
      "screen": "tmdb_random_list"
    }
  ]
}
```

Notes:

- Lors de l'entree dans une fenetre, une seule execution est lancee:
  - POST /api/config avec {"auto_rotate": false}
  - puis POST /api/display-image
- Pendant la fenetre, aucune nouvelle execution n'est lancee.
- A la sortie de la fenetre:
  - POST /api/config avec {"auto_rotate": true}
  - puis POST /api/rotate

## Dépannage

### Aucun log Python dans Docker

Vérifier:

```bash
docker logs --tail 200 condescending_rubin
docker exec condescending_rubin sh -lc "cat -n /etc/crontab"
```

Tester manuellement la commande en tant que `appuser`:

```bash
docker exec condescending_rubin sh -lc "su -s /bin/sh appuser -c '/usr/local/bin/python3 /app/main.py'"
```

### Erreur WSL: Cannot connect to the Docker daemon

Si `docker build` échoue dans WSL mais fonctionne dans PowerShell:

- Activer l'intégration WSL dans Docker Desktop.
- Ou exécuter les commandes Docker depuis PowerShell dans le dossier du projet.

### Endpoint du cadre inaccessible

Sous WSL, la résolution `.local` peut échouer. Utiliser une IP directe dans `PHOTOFRAME_API_URLS`, par exemple:

```text
PHOTOFRAME_API_URLS=http://192.168.1.50/api/
```

### Cron actif mais "aucune fenetre active"

Si les logs affichent souvent `Planning charge: aucune fenetre active...`, verifier la timezone utilisee dans le conteneur.

- Les fenetres du `schedule.json` sont comparees a l'heure locale du scheduler.
- En Docker, sans configuration, l'heure peut etre differente (souvent UTC).

Configurer dans `.env`:

```text
SCREEN_SCHEDULE_TIMEZONE=Europe/Paris
TZ=Europe/Paris
```

## Arborescence utile

- `main.py`: point d'entrée CLI.
- `frame.py`: chargement `.env` + envoi image vers le cadre.
- `screen_scheduler.py`: moteur de planning.
- `screens/`: implémentations d'écrans.
- `schedule.json`: règles de déclenchement.
- `Dockerfile` + `supervisord.conf`: runtime conteneur.
