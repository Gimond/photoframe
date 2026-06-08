import os
import json
from datetime import datetime, timedelta
from functools import lru_cache

from PIL import Image, ImageDraw

import frame
from screens.weather_utils import (
    build_temp_trend,
    build_two_hour_slots,
    fetch_calendar_events,
    fetch_openmeteo_points,
    format_temp,
)
from screens.utils import (
    capitalize_first,
    DEFAULT_CALENDAR_EVENT_COLORS,
    draw_calendar_event_row,
    draw_header_line,
    draw_overflow_ellipsis,
    format_date_fr,
    load_font,
    openweather_icon_to_svg,
    paste_image_fit_box,
    paste_svg_icon,
)


CITY = os.getenv("CITY", "Massingy,fr")
GOOGLE_CALENDAR_ICS_URL = os.getenv("GOOGLE_CALENDAR_ICS_URL", "")
GOOGLE_CALENDAR_ICS_URLS = [
    url.strip()
    for url in os.getenv("GOOGLE_CALENDAR_ICS_URLS", GOOGLE_CALENDAR_ICS_URL).split(",")
    if url.strip()
]

BEAR_IMAGES_DIR = os.path.join(frame.SCRIPT_DIR, "images", "bear")
BEAR_DISPLAY_CONFIG_PATH = os.path.join(frame.SCRIPT_DIR, "bear_display_config.json")
CALENDAR_EVENT_COLORS = DEFAULT_CALENDAR_EVENT_COLORS
SIMULATE_LONG_EVENTS = os.getenv("SIMULATE_LONG_EVENTS", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
BEAR_DEBUG_LOGS = os.getenv("BEAR_DEBUG_LOGS", "1").strip().lower() in {"1", "true", "yes", "on"}

DEFAULT_BEAR_DISPLAY_CONFIG = {
    "available_images": [
        "cold_clear.png",
        "cool_clear.png",
        "hot_sunny.png",
        "mild_clear.png",
        "rainy_cold.png",
        "rainy_mild.png",
        "snow_freezing.png",
        "very_cold.png",
        "warm_sunny.png",
    ],
    "default_image": "mild_clear.png",
    "snow_icon_prefixes": ["13"],
    "rain_icon_prefixes": ["09", "10", "11"],
    "snow_image": "snow_freezing.png",
    "rainy_temperature_rules": [
        {"avg_temp_max": 8, "image": "rainy_cold.png"},
        {"avg_temp_min": 8, "image": "rainy_mild.png"},
    ],
    "temperature_rules": [
        {"avg_temp_min": 30, "image": "hot_sunny.png"},
        {"avg_temp_min": 24, "image": "warm_sunny.png"},
        {"avg_temp_min": 18, "image": "mild_clear.png"},
        {"avg_temp_min": 12, "image": "cool_clear.png"},
        {"avg_temp_min": 6, "image": "cold_clear.png"},
        {"avg_temp_min": -999, "image": "very_cold.png"},
    ],
}


def _log(message, always=False):
    if always or BEAR_DEBUG_LOGS:
        print(f"[meteo_calendar_bear] {message}", flush=True)


def _normalize_temperature_rules(rules):
    normalized = []
    for rule in rules or []:
        if not isinstance(rule, dict) or not rule.get("image"):
            continue

        avg_temp_min = rule.get("avg_temp_min")
        avg_temp_max = rule.get("avg_temp_max")

        if not isinstance(avg_temp_min, (int, float)) and not isinstance(avg_temp_max, (int, float)):
            continue

        normalized.append(
            {
                "avg_temp_min": avg_temp_min,
                "avg_temp_max": avg_temp_max,
                "image": rule["image"],
            }
        )

    return sorted(
        normalized,
        key=lambda rule: (
            rule["avg_temp_min"] if isinstance(rule.get("avg_temp_min"), (int, float)) else float("-inf"),
            rule["avg_temp_max"] if isinstance(rule.get("avg_temp_max"), (int, float)) else float("inf"),
        ),
        reverse=True,
    )


def _select_image_from_rules(rules, avg_temp, default_image):
    if avg_temp is None:
        return default_image

    for rule in rules:
        avg_temp_min = rule.get("avg_temp_min")
        avg_temp_max = rule.get("avg_temp_max")
        if isinstance(avg_temp_min, (int, float)) and avg_temp < avg_temp_min:
            continue
        if isinstance(avg_temp_max, (int, float)) and avg_temp > avg_temp_max:
            continue

        return rule["image"]

    return default_image


def _compute_avg_temp_8_to_20(hourly_slots):
    temps = []
    for slot in hourly_slots or []:
        if not isinstance(slot, dict):
            continue
        hour = slot.get("hour")
        temp = slot.get("temp")
        if not isinstance(hour, int) or not isinstance(temp, (int, float)):
            continue
        if 8 <= hour <= 20:
            temps.append(float(temp))

    if not temps:
        return None
    return sum(temps) / len(temps)


@lru_cache(maxsize=1)
def load_bear_display_config():
    config = DEFAULT_BEAR_DISPLAY_CONFIG
    try:
        with open(BEAR_DISPLAY_CONFIG_PATH, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            config = {
                **DEFAULT_BEAR_DISPLAY_CONFIG,
                **loaded,
            }
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError) as exc:
        _log(f"Config ours invalide ({exc}), utilisation des regles par defaut.", always=True)

    if not isinstance(config.get("available_images"), list):
        config["available_images"] = DEFAULT_BEAR_DISPLAY_CONFIG["available_images"]
    config["temperature_rules"] = _normalize_temperature_rules(
        config.get("temperature_rules") or DEFAULT_BEAR_DISPLAY_CONFIG["temperature_rules"]
    )

    rainy_rules = _normalize_temperature_rules(config.get("rainy_temperature_rules"))
    if not rainy_rules:
        rainy_rules = _normalize_temperature_rules(DEFAULT_BEAR_DISPLAY_CONFIG["rainy_temperature_rules"])
    config["rainy_temperature_rules"] = rainy_rules
    return config


def select_bear_illustration(weather):
    config = load_bear_display_config()
    available = set(config.get("available_images") or [])

    hourly = weather.get("hourly") or []
    icons = [(slot.get("icon") or "").lower() for slot in hourly]
    avg_temp = _compute_avg_temp_8_to_20(hourly)

    snow_prefixes = tuple(config.get("snow_icon_prefixes") or ["13"])
    rain_prefixes = tuple(config.get("rain_icon_prefixes") or ["09", "10", "11"])

    has_snow = any(icon.startswith(snow_prefixes) for icon in icons)
    has_rain = any(icon.startswith(rain_prefixes) for icon in icons)

    if has_snow:
        filename = config.get("snow_image") or config.get("default_image") or "mild_clear.png"
    elif has_rain:
        filename = _select_image_from_rules(
            config.get("rainy_temperature_rules") or [],
            avg_temp,
            config.get("default_image") or "mild_clear.png",
        )
    elif avg_temp is None:
        filename = config.get("default_image") or "mild_clear.png"
    else:
        filename = _select_image_from_rules(
            config.get("temperature_rules") or [],
            avg_temp,
            config.get("default_image") or "mild_clear.png",
        )

    if filename not in available:
        filename = config.get("default_image") or "mild_clear.png"

    path = os.path.join(BEAR_IMAGES_DIR, filename)
    if os.path.exists(path):
        _log(f"Selection ours: {filename} (avg_temp={avg_temp}, rain={has_rain}, snow={has_snow})")
        return path

    _log(f"Image ours introuvable: {path}", always=True)
    return None


def build_simulated_events():
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    today_date = now.date()
    tomorrow_date = today_date + timedelta(days=1)

    base_titles = [
        "Point projet trimestriel avec equipe produit et partenaires externes",
        "Revue architecture backend et alignement roadmap technique",
        "Atelier priorisation des demandes clients a forte criticite",
        "Session design interface e-ink lisibilite et contraste",
        "Comite pilotage budget et planification des livraisons",
        "Synchronisation multi-equipes dependances et arbitrages",
        "Preparation demo hebdomadaire avec scenarios de secours",
        "Retrospective sprint actions correctives et suivi qualite",
        "Reunion transverse incidents production et plan de remediation",
    ]

    events = []

    simulated_count = 9
    for i in range(simulated_count):
        hour = 8 + i
        start = datetime.combine(tomorrow_date, datetime.min.time()).replace(hour=hour, minute=30)

        events.append(
            {
                "start": start,
                "summary": f"{base_titles[i % len(base_titles)]} - segment {i + 1}",
                "source_index": i % 2,
            }
        )

    return events


def get_weather():
    try:
        _, _, _, tomorrow_points = fetch_openmeteo_points(CITY)

        if not tomorrow_points:
            raise ValueError("Aucune prevision disponible pour demain")

        if tomorrow_points:
            day_min = round(min(p["temp"] for p in tomorrow_points))
            day_max = round(max(p["temp"] for p in tomorrow_points))
        else:
            day_min = "N/A"
            day_max = "N/A"

        weather = {
            "day_min": day_min,
            "day_max": day_max,
            "hourly": build_two_hour_slots(tomorrow_points),
            "day_trend": build_temp_trend(tomorrow_points, start_hour=7.5, end_hour=22.5),
        }
        _log(
            f"Meteo demain OK pour {CITY}: points={len(tomorrow_points)}, hourly={len(weather['hourly'])}, "
            f"min={day_min}, max={day_max}"
        )
        return weather
    except Exception as exc:
        _log(f"Meteo indisponible ({exc}), utilisation d'une valeur par defaut.", always=True)
        return {
            "day_min": "N/A",
            "day_max": "N/A",
            "hourly": [],
            "day_trend": [],
        }


def get_events():
    _, tomorrow_events, had_success, _ = fetch_calendar_events(GOOGLE_CALENDAR_ICS_URLS, log_fn=_log)
    if not had_success:
        return []
    return tomorrow_events


def render_image(weather, events, output_path=None):
    target_path = os.path.abspath(output_path) if output_path else frame.DEFAULT_IMAGE_PATH

    img = Image.new("RGB", (800, 480), color="black")
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"  # disable text antialiasing for crisp e-ink rendering

    font_large = load_font(30, bold=True)
    font_small = load_font(22)
    font_title = load_font(24, bold=True)
    font_tiny = load_font(19, bold=True)

    today_events = events

    if SIMULATE_LONG_EVENTS:
        today_events = build_simulated_events()

    card_margin = 10
    card_radius = 12
    today_card = (card_margin, card_margin, img.width - card_margin, img.height - card_margin)
    draw.rounded_rectangle(today_card, radius=card_radius, fill="white")

    y = draw_header_line(
        draw,
        22,
        capitalize_first(format_date_fr(datetime.now() + timedelta(days=1))),
        f"{weather['day_min']}° / {weather['day_max']}°",
        font_large,
        font_large,
        img.width,
    )
    y += 10

    if weather["hourly"]:
        weather_box_left = today_card[0]
        weather_box_right = today_card[2]
        weather_box_top = y - 5
        weather_box_bottom = y + 120
        weather_content_top_padding = 10
        weather_content_bottom_padding = 10
        draw.rectangle(
            (weather_box_left, weather_box_top, weather_box_right, weather_box_bottom),
            fill="#D2D2D2",
        )

        # Full-width temperature trend behind icons/text, mapped over 07:30 -> 22:30.
        trend = weather.get("day_trend") or []
        if trend:
            temps = [p["temp"] for p in trend]
            t_min = min(temps)
            t_max = max(temps)
            y_top = weather_box_top + 20 + weather_content_top_padding
            y_bottom = weather_box_top + 94 + weather_content_top_padding + weather_content_bottom_padding

            trend_left = weather_box_left + 2
            trend_right = weather_box_right - 2
            trend_width = max(1, trend_right - trend_left)

            points = []
            for p in trend:
                ratio_x = (p["hour"] - 7.5) / (22.5 - 7.5)
                x = int(round(trend_left + (ratio_x * trend_width)))

                if t_max == t_min:
                    ratio_y = 0.5
                else:
                    ratio_y = (p["temp"] - t_min) / (t_max - t_min)
                y_curve = int(round(y_bottom - (ratio_y * (y_bottom - y_top))))
                points.append((x, y_curve))

            if len(points) >= 2:
                draw.line(points, fill="white", width=8)

        left_margin = weather_box_left + 8
        right_margin = img.width - weather_box_right + 8
        available_width = img.width - left_margin - right_margin
        count = len(weather["hourly"])
        step = available_width / max(count, 1)

        for i, slot in enumerate(weather["hourly"]):
            center_x = left_margin + (step * i) + (step / 2)

            icon_path = openweather_icon_to_svg(slot["icon"])
            paste_svg_icon(img, icon_path, center_x, y - 5 + weather_content_top_padding, size=56)

            hour_text = f"{slot['hour']:02d}h"
            temp_value = format_temp(slot["temp"])
            temp_text = f"{temp_value}°C" if temp_value != "--" else "--"
            hour_bbox = draw.textbbox((0, 0), hour_text, font=font_tiny)
            temp_bbox = draw.textbbox((0, 0), temp_text, font=font_tiny)
            hour_x = int(round(center_x - (hour_bbox[2] - hour_bbox[0]) / 2))
            temp_x = int(round(center_x - (temp_bbox[2] - temp_bbox[0]) / 2))
            draw.text((hour_x, y + 50 + weather_content_top_padding), hour_text, fill="black", font=font_tiny)
            draw.text((temp_x, y + 77 + weather_content_top_padding), temp_text, fill="black", font=font_tiny)

        y += 140
    else:
        draw.text((20, y), "Previsions horaires indisponibles.", fill="black", font=font_small)
        y += 34

    # Keep only tomorrow's events and render them in the left half.
    today = (datetime.now() + timedelta(days=1)).date()
    shown_today = [event for event in today_events if event["start"].date() == today]
    hidden_today = 0

    max_y_today = img.height - card_margin - 10
    outer_left_margin = 20
    outer_right_margin = 20
    content_w = img.width - outer_left_margin - outer_right_margin
    col_w = int(content_w / 2)
    left_x = outer_left_margin
    today_y_start = y
    last_rendered_today = None

    y_left = today_y_start
    for event in shown_today:
        if y_left + 30 > max_y_today:
            hidden_today += 1
            continue
        last_rendered_today = {"y": y_left, "x": left_x}
        y_left = draw_calendar_event_row(
            draw,
            y_left,
            event,
            font_tiny,
            left_x,
            left_x + col_w,
            CALENDAR_EVENT_COLORS,
        )

    if not shown_today and today_y_start + 30 <= max_y_today:
        draw.text((left_x, today_y_start), "Aucun evenement.", fill="black", font=font_small)

    if hidden_today > 0 and last_rendered_today:
        draw_overflow_ellipsis(draw, last_rendered_today["y"] - 5, last_rendered_today["x"], font_tiny)

    right_box = (
        outer_left_margin + col_w,
        today_y_start - 10,
        img.width - outer_right_margin,
        img.height - card_margin + 5,
    )
    bear_path = select_bear_illustration(weather)
    paste_image_fit_box(img, bear_path, right_box, margin=0)

    img.save(target_path)
    return target_path


def generate_image(output_path=None):
    weather = get_weather()
    events = get_events()
    return render_image(weather, events, output_path=output_path)
