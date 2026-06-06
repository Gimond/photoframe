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

# API de base du cadre (terminer sur /api ou /api/), surchargeable via PHOTOFRAME_API_URLS.
PHOTOFRAME_API_URLS = os.getenv("PHOTOFRAME_API_URLS", "http://photoframe.local/api/")
DEFAULT_IMAGE_PATH = os.path.join(SCRIPT_DIR, "screen.png")


def _split_urls(value):
    if not value:
        return []
    return [u.strip() for u in value.split(",") if u.strip()]


def _normalize_api_base_url(value):
    api_base = value.strip().rstrip("/")
    for suffix in ("/display-image", "/config", "/rotate"):
        if api_base.endswith(suffix):
            api_base = api_base[: -len(suffix)]
            break
    if not api_base.endswith("/api"):
        api_base = f"{api_base}/api"
    return api_base


def _configured_api_bases():
    configured = _split_urls(PHOTOFRAME_API_URLS)
    if not configured:
        return []
    return list(dict.fromkeys(_normalize_api_base_url(url) for url in configured))


def _host_from_url(url):
    return url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]


def _post_json_on_bases(path, payload):
    bases = _configured_api_bases()
    if not bases:
        print("PHOTOFRAME_API_URLS est vide. Configure au moins une URL API de base.")
        return False

    print(f"Tentative sur {len(bases)} endpoint(s) PhotoFrame")
    last_error = None

    for base in bases:
        api_url = f"{base}/{path.lstrip('/')}"
        try:
            socket.getaddrinfo(_host_from_url(api_url), None)
        except socket.gaierror:
            print(f"Hote non resolu (url: {api_url})")
            last_error = f"resolution DNS impossible pour {api_url}"
            continue

        try:
            response = requests.post(api_url, json=payload, timeout=15)
            if response.status_code == 200:
                print(f"Requete envoyee via {api_url}")
                return True
            body = (response.text or "").strip()
            print(f"API erreur {response.status_code} sur {api_url}: {body[:300]}")
            last_error = f"HTTP {response.status_code}"
        except requests.RequestException as exc:
            print(f"Requete echouee sur {api_url}: {exc}")
            last_error = str(exc)

    if last_error:
        print(f"Derniere erreur: {last_error}")
    return False


def configure_photoframe_auto_rotate(enabled):
    return _post_json_on_bases("config", {"auto_rotate": bool(enabled)})


def rotate_photoframe():
    return _post_json_on_bases("rotate", {})


def send_to_photoframe(image_path=None):
    target_path = image_path or DEFAULT_IMAGE_PATH

    if not os.path.exists(target_path):
        print(f"Image introuvable: {target_path}")
        return False

    mime_type = mimetypes.guess_type(target_path)[0] or "image/png"
    bases = _configured_api_bases()
    if not bases:
        print("PHOTOFRAME_API_URLS est vide. Configure au moins une URL API de base.")
        return False

    print(f"Tentative sur {len(bases)} endpoint(s) PhotoFrame")
    last_error = None

    for base in bases:
        api_url = f"{base}/display-image"
        try:
            socket.getaddrinfo(_host_from_url(api_url), None)
        except socket.gaierror:
            print(f"Hote non resolu (url: {api_url})")
            last_error = f"resolution DNS impossible pour {api_url}"
            continue

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
            if isinstance(exc, requests.ReadTimeout):
                print(f"Timeout de lecture sur {api_url} apres upload; image probablement affichee.")
                return True
            print(f"Envoi direct echoue sur {api_url} ({exc}), tentative multipart...")
            last_error = str(exc)

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
        "http://192.168.1.50/api/"
    )
    if last_error:
        print(f"Derniere erreur: {last_error}")
    return False
