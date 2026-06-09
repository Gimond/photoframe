import json
import os
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request

import frame
from screen_scheduler import build_rules, load_schedule

BEAR_DISPLAY_CONFIG_PATH = Path(frame.SCRIPT_DIR) / "bear_display_config.json"
SCHEDULE_PATH = Path(frame.SCRIPT_DIR) / "schedule.json"
BEAR_IMAGES_DIR = Path(frame.SCRIPT_DIR) / "images" / "bear"
ADMIN_PORT = int(os.getenv("ADMIN_PORT", "8080"))
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")

SCREEN_CHOICES = [
    "meteo_calendar_glance",
    "meteo_calendar_bear_today",
    "meteo_calendar_bear_tomorrow",
    "tmdb_random_list",
]

DAY_CHOICES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

app = Flask(__name__, template_folder="templates")


def _check_secret():
    if not ADMIN_SECRET:
        return
    token = request.headers.get("X-Admin-Secret") or request.args.get("secret", "")
    if token != ADMIN_SECRET:
        abort(403)


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def _load_bear_config():
    try:
        with BEAR_DISPLAY_CONFIG_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _load_schedule():
    try:
        with SCHEDULE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"default": "meteo_calendar_glance", "rules": []}


def _available_bear_images():
    if not BEAR_IMAGES_DIR.exists():
        return []
    return sorted(p.name for p in BEAR_IMAGES_DIR.glob("*.png"))


# ---------------------------------------------------------------------------
# Routes – UI
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    _check_secret()
    bear_config = _load_bear_config()
    schedule = _load_schedule()
    return render_template(
        "admin/index.html",
        bear_config=bear_config,
        schedule=schedule,
        screen_choices=SCREEN_CHOICES,
        day_choices=DAY_CHOICES,
        available_images=_available_bear_images(),
    )


# ---------------------------------------------------------------------------
# Routes – Schedule API
# ---------------------------------------------------------------------------

@app.get("/api/schedule")
def get_schedule():
    _check_secret()
    return jsonify(_load_schedule())


@app.post("/api/schedule")
def save_schedule():
    _check_secret()
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON invalide"}), 400

    # Validate through existing parser before writing.
    try:
        build_rules(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    with SCHEDULE_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Routes – Bear display config API
# ---------------------------------------------------------------------------

@app.get("/api/bear-config")
def get_bear_config():
    _check_secret()
    return jsonify(_load_bear_config())


@app.post("/api/bear-config")
def save_bear_config():
    _check_secret()
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON invalide"}), 400

    available = _available_bear_images()

    # Basic validation
    errors = []
    if "default_image" in data and data["default_image"] not in available:
        errors.append(f"Image inconnue: {data['default_image']}")
    if "snow_image" in data and data["snow_image"] not in available:
        errors.append(f"Image inconnue: {data['snow_image']}")
    for rule in data.get("temperature_rules", []) + data.get("rainy_temperature_rules", []):
        if not isinstance(rule, dict):
            errors.append("Regle invalide (pas un objet)")
            continue
        if rule.get("image") not in available:
            errors.append(f"Image inconnue dans regle: {rule.get('image')}")
    if errors:
        return jsonify({"error": "; ".join(errors)}), 400

    with BEAR_DISPLAY_CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Entry point (dev only)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=ADMIN_PORT, debug=False)
