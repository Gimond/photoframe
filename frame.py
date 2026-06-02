import requests
import os
import mimetypes
import socket


def _load_env_file(env_path):
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if (
                    (value.startswith('"') and value.endswith('"'))
                    or (value.startswith("'") and value.endswith("'"))
                ):
                    value = value[1:-1]
                if key:
                    os.environ.setdefault(key, value)
    except OSError as exc:
        print(f"Impossible de charger le fichier .env ({exc})")

# --- CONFIGURATION ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_load_env_file(os.path.join(SCRIPT_DIR, ".env"))

# API cible pour afficher immédiatement une image uploadée.
# Peut etre surchargee via PHOTOFRAME_API_URLS (liste separee par des virgules).
PHOTOFRAME_API_URLS = os.getenv("PHOTOFRAME_API_URLS", "http://photoframe.local/api/display-image")
DEFAULT_IMAGE_PATH = os.path.join(SCRIPT_DIR, "screen.png")
# --- Envoi commun au cadre ---
def send_to_photoframe(image_path=None):
    target_path = image_path or DEFAULT_IMAGE_PATH

    if not os.path.exists(target_path):
        print(f"Image introuvable: {target_path}")
        return False

    mime_type = mimetypes.guess_type(target_path)[0] or "image/png"

    def _split_urls(value):
        if not value:
            return []
        return [u.strip() for u in value.split(",") if u.strip()]

    configured_urls = _split_urls(PHOTOFRAME_API_URLS)
    if not configured_urls:
        print("PHOTOFRAME_API_URLS est vide. Configure au moins une URL d'endpoint.")
        return False

    # Conserve l'ordre tout en supprimant les doublons.
    candidate_urls = list(dict.fromkeys(configured_urls))
    print(f"Tentative sur {len(candidate_urls)} endpoint(s) PhotoFrame")

    last_error = None

    for api_url in candidate_urls:
        # Detecte rapidement les hotes non resolvables (mDNS souvent capricieux sous WSL).
        try:
            host = api_url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
            socket.getaddrinfo(host, None)
        except socket.gaierror:
            print(f"Hote non resolu: {host} (url: {api_url})")
            last_error = f"resolution DNS impossible pour {host}"
            continue

        # Format prioritaire selon la doc: body binaire + Content-Type image/*
        try:
            with open(target_path, "rb") as f:
                payload = f.read()
            response = requests.post(
                api_url,
                data=payload,
                headers={"Content-Type": mime_type},
                timeout=30,
            )
            if response.status_code == 200:
                print(f"Image envoyee via {api_url}")
                return True
            body = (response.text or "").strip()
            print(f"API erreur {response.status_code} sur {api_url}: {body[:300]}")
            last_error = f"HTTP {response.status_code}"
        except requests.RequestException as exc:
            # Le firmware peut afficher l'image puis ne jamais renvoyer de body
            # avant le timeout HTTP. Dans ce cas, on considere l'operation reussie.
            if isinstance(exc, requests.ReadTimeout):
                print(f"Timeout de lecture sur {api_url} apres upload; image probablement affichee.")
                return True
            print(f"Envoi direct echoue sur {api_url} ({exc}), tentative multipart...")
            last_error = str(exc)

        # Fallback robuste: multipart/form-data (egalement supporte par l'API)
        try:
            with open(target_path, "rb") as f:
                files = {"image": (os.path.basename(target_path), f, mime_type)}
                response = requests.post(api_url, files=files, timeout=30)
            if response.status_code == 200:
                print(f"Image envoyee via {api_url} (multipart)")
                return True

            body = (response.text or "").strip()
            print(f"API erreur {response.status_code} sur {api_url}: {body[:300]}")
            last_error = f"HTTP {response.status_code}"
        except requests.RequestException as exc:
            if isinstance(exc, requests.ReadTimeout):
                print(f"Timeout de lecture sur {api_url} (multipart) apres upload; image probablement affichee.")
                return True
            print(f"Envoi multipart echoue sur {api_url}: {exc}")
            last_error = str(exc)

    print(
        "Aucun endpoint PhotoFrame joignable. "
        "Sous WSL, la resolution de *.local peut echouer: "
        "definis PHOTOFRAME_API_URLS avec l'IP du cadre, ex: "
        "http://192.168.1.50/api/display-image"
    )
    if last_error:
        print(f"Derniere erreur: {last_error}")
    return False
