import os
import json
from datetime import datetime, timedelta
from functools import lru_cache
from io import BytesIO

import cairosvg
import requests
from PIL import Image, ImageDraw, ImageFont
from icalendar import Calendar
import recurring_ical_events

import frame
from screens.weather_utils import (
    build_temp_trend,
    build_two_hour_slots,
    fetch_openmeteo_points,
    format_temp,
)


CITY = os.getenv("CITY", "Massingy,fr")
GOOGLE_CALENDAR_ICS_URL = os.getenv("GOOGLE_CALENDAR_ICS_URL", "")
GOOGLE_CALENDAR_ICS_URLS = [
    url.strip()
    for url in os.getenv("GOOGLE_CALENDAR_ICS_URLS", GOOGLE_CALENDAR_ICS_URL).split(",")
    if url.strip()
]

SVG_DIR = os.path.join(frame.SCRIPT_DIR, "svg")
FONTS_DIR = os.path.join(frame.SCRIPT_DIR, "fonts")
BEAR_IMAGES_DIR = os.path.join(frame.SCRIPT_DIR, "images", "bear")
BEAR_DISPLAY_CONFIG_PATH = os.path.join(frame.SCRIPT_DIR, "bear_display_config.json")
CALENDAR_EVENT_COLORS = ["#F2C94C", "#9B51E0"]
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


def load_font(size, bold=False):
    weight = "Bold" if bold else "Regular"

    # Pick the closest source variant to avoid stretching a 16px master to 20px,
    # which can look clipped/fragile on 1-bit e-ink text rendering.
    variant_sizes = ["12", "16", "21"]
    preferred_sizes = sorted(variant_sizes, key=lambda v: abs(int(v) - size))

    for variant_size in preferred_sizes:
        font_path = os.path.join(FONTS_DIR, f"TRMNL{variant_size}-{weight}.ttf")
        if os.path.exists(font_path):
            try:
                return ImageFont.truetype(font_path, size)
            except OSError:
                continue

    raise FileNotFoundError(
        f"Police TRMNL introuvable ou illisible dans {FONTS_DIR} (poids {weight})."
    )


def format_date_fr(dt):
    weekdays = [
        "lundi",
        "mardi",
        "mercredi",
        "jeudi",
        "vendredi",
        "samedi",
        "dimanche",
    ]
    months = [
        "janvier",
        "fevrier",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "aout",
        "septembre",
        "octobre",
        "novembre",
        "decembre",
    ]
    weekday = weekdays[dt.weekday()]
    month = months[dt.month - 1]
    return f"{weekday} {dt.day:02d} {month} {dt.year}"


def capitalize_first(text):
    if not text:
        return text
    return text[0].upper() + text[1:]


def openweather_icon_to_svg(icon_code):
    icon = (icon_code or "").strip().lower()

    if len(icon) == 2 and icon.isdigit():
        icon = f"{icon}d"

    mapping = {
        "01d": "wi-day-sunny.svg",
        "01n": "wi-night-clear.svg",
        "02d": "wi-day-sunny-overcast.svg",
        "02n": "wi-night-alt-partly-cloudy.svg",
        "03d": "wi-day-cloudy.svg",
        "03n": "wi-night-alt-cloudy.svg",
        "04d": "wi-cloudy.svg",
        "04n": "wi-night-cloudy.svg",
        "09d": "wi-day-showers.svg",
        "09n": "wi-night-alt-showers.svg",
        "10d": "wi-day-rain.svg",
        "10n": "wi-night-alt-rain.svg",
        "11d": "wi-day-thunderstorm.svg",
        "11n": "wi-night-alt-thunderstorm.svg",
        "13d": "wi-day-snow.svg",
        "13n": "wi-night-alt-snow.svg",
        "50d": "wi-day-fog.svg",
        "50n": "wi-night-fog.svg",
    }

    filename = mapping.get(icon)
    if not filename:
        prefix = icon[:2]
        if prefix in {"01", "02", "03", "04", "09", "10", "11", "13", "50"}:
            filename = mapping.get(f"{prefix}d")
        else:
            filename = "wi-na.svg"

    return os.path.join(SVG_DIR, filename)


def paste_svg_icon(base_image, icon_path, center_x, top_y, size):
    if not os.path.exists(icon_path):
        raise FileNotFoundError(f"Icone SVG introuvable: {icon_path}")

    try:
        png_bytes = cairosvg.svg2png(url=icon_path, output_width=size, output_height=size)
        icon_img = Image.open(BytesIO(png_bytes)).convert("RGBA")
        left = int(center_x - (size / 2))
        base_image.paste(icon_img, (left, int(top_y)), icon_img)
    except Exception as exc:
        raise RuntimeError(f"Impossible de rendre l'icone SVG {icon_path}: {exc}") from exc


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


def paste_bear_illustration(base_image, image_path, box, margin=10):
    if not image_path or not os.path.exists(image_path):
        return

    x1, y1, x2, y2 = box
    max_w = max(1, int(x2 - x1 - (2 * margin)))
    max_h = max(1, int(y2 - y1 - (2 * margin)))

    with Image.open(image_path) as src:
        bear = src.convert("RGBA")
        scale = min(max_w / bear.width, max_h / bear.height)
        new_w = max(1, int(round(bear.width * scale)))
        new_h = max(1, int(round(bear.height * scale)))
        resized = bear.resize((new_w, new_h), Image.Resampling.LANCZOS)

    paste_x = int(round(x1 + ((x2 - x1 - new_w) / 2)))
    paste_y = int(round(y1 + ((y2 - y1 - new_h) / 2)))
    base_image.paste(resized, (paste_x, paste_y), resized)


def truncate_to_width(draw, text, font, max_width):
    value = text or ""
    if draw.textbbox((0, 0), value, font=font)[2] <= max_width:
        return value

    ellipsis = "..."
    while value:
        value = value[:-1].rstrip()
        candidate = value + ellipsis
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            return candidate
    return ellipsis


def draw_header_line(draw, y, left_text, right_text, font_left, font_right, width):
    draw.text((20, y), left_text, fill="black", font=font_left)
    right_bbox = draw.textbbox((0, 0), right_text, font=font_right)
    right_w = right_bbox[2] - right_bbox[0]
    right_x = int(round(width - 20 - right_w))
    draw.text((right_x, y), right_text, fill="black", font=font_right)
    return y + 46


def draw_calendar_event_row(draw, y, event, font_tiny, start_x, max_x):
    accent = CALENDAR_EVENT_COLORS[event["source_index"] % len(CALENDAR_EVENT_COLORS)]
    time_text = event["start"].strftime("%H:%M")
    pill_w = 72
    pill_h = 20
    pill_x1 = start_x
    pill_y1 = y + 4
    pill_x2 = pill_x1 + pill_w
    pill_y2 = pill_y1 + pill_h

    time_bbox = draw.textbbox((0, 0), time_text, font=font_tiny)
    time_w = time_bbox[2] - time_bbox[0]
    time_h = time_bbox[3] - time_bbox[1]
    time_x = int(round(pill_x1 + (pill_w - time_w) / 2))
    time_y = int(round(pill_y1 + (pill_h - time_h) / 2 - 4))

    summary_x = pill_x2 + 14
    summary_max_w = max(10, max_x - summary_x)
    summary_text = truncate_to_width(draw, event["summary"], font_tiny, summary_max_w)

    draw.rounded_rectangle((pill_x1, pill_y1, pill_x2, pill_y2), radius=7, fill=accent)
    draw.text((time_x, time_y), time_text, fill="black", font=font_tiny)
    draw.text((summary_x, y + 3), summary_text, fill="black", font=font_tiny)
    return y + 30


def draw_plus_circle_icon(draw, center_x, center_y, radius=12):
    x1 = center_x - radius
    y1 = center_y - radius
    x2 = center_x + radius
    y2 = center_y + radius
    draw.ellipse((x1, y1, x2, y2), fill="black", outline="white", width=2)

    arm = max(4, radius - 6)
    draw.line((center_x - arm, center_y, center_x + arm, center_y), fill="white", width=2)
    draw.line((center_x, center_y - arm, center_x, center_y + arm), fill="white", width=2)


def draw_overflow_ellipsis(draw, event_row_y, start_x, font_tiny):
    pill_w = 72
    ellipsis = "..."
    bbox = draw.textbbox((0, 0), ellipsis, font=font_tiny)
    text_w = bbox[2] - bbox[0]
    text_x = int(round(start_x + (pill_w - text_w) / 2))
    text_y = event_row_y + 22
    draw.text((text_x, text_y), ellipsis, fill="black", font=font_tiny)


def build_simulated_events():
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    today_date = now.date()

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
        start = datetime.combine(today_date, datetime.min.time()).replace(hour=hour, minute=30)

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
        _, _, today_points, _ = fetch_openmeteo_points(CITY)

        if not today_points:
            raise ValueError("Aucune prevision disponible pour aujourd'hui")

        if today_points:
            today_min = round(min(p["temp"] for p in today_points))
            today_max = round(max(p["temp"] for p in today_points))
        else:
            today_min = "N/A"
            today_max = "N/A"

        weather = {
            "today_min": today_min,
            "today_max": today_max,
            "hourly": build_two_hour_slots(today_points),
            "today_trend": build_temp_trend(today_points, start_hour=7.5, end_hour=22.5),
        }
        _log(
            f"Meteo OK pour {CITY}: points={len(today_points)}, hourly={len(weather['hourly'])}, "
            f"min={today_min}, max={today_max}"
        )
        return weather
    except Exception as exc:
        _log(f"Meteo indisponible ({exc}), utilisation d'une valeur par defaut.", always=True)
        return {
            "today_min": "N/A",
            "today_max": "N/A",
            "hourly": [],
            "today_trend": [],
        }


def get_events():
    events = []
    seen = set()
    had_success = False
    had_error = False
    now = datetime.now()
    today = now.date()
    tomorrow = today + timedelta(days=1)
    range_start = datetime.combine(today, datetime.min.time())
    range_end = datetime.combine(tomorrow + timedelta(days=1), datetime.min.time())

    for calendar_index, calendar_url in enumerate(GOOGLE_CALENDAR_ICS_URLS):
        added_for_calendar = 0
        try:
            response = requests.get(calendar_url, timeout=10)
            response.raise_for_status()
            cal = Calendar.from_ical(response.text)
            had_success = True

            # Expand recurring events inside [today, tomorrow + 1 day).
            expanded = recurring_ical_events.of(cal).between(range_start, range_end)

            for component in expanded:
                if component.name != "VEVENT":
                    continue

                # Un evenement mal forme ne doit pas invalider tout le calendrier.
                try:
                    dtstart = component.get("dtstart")
                    if dtstart is None:
                        continue
                    start_raw = dtstart.dt

                    if isinstance(start_raw, datetime):
                        start = start_raw
                        if start.tzinfo is not None:
                            start = start.astimezone().replace(tzinfo=None)
                    else:
                        start = datetime.combine(start_raw, datetime.min.time())

                    summary_value = component.get("summary")
                    summary = str(summary_value) if summary_value else "(Sans titre)"

                    if start.date() in (today, tomorrow):
                        event_key = (start.isoformat(), summary)
                        if event_key not in seen:
                            seen.add(event_key)
                            events.append(
                                {
                                    "start": start,
                                    "summary": summary,
                                    "source_index": calendar_index,
                                }
                            )
                            added_for_calendar += 1
                except Exception as exc:
                    had_error = True
                    _log(f"Evenement ignore ({exc}) : {calendar_url}", always=True)

            _log(f"Calendrier charge ({added_for_calendar} evenement(s)) : {calendar_url}")
        except Exception as exc:
            had_error = True
            _log(f"Calendrier indisponible ({exc}) : {calendar_url}", always=True)

    if not had_success:
        _log("Aucun calendrier accessible, aucun evenement affiche.", always=True)
        return [], []

    if events and all(event["summary"].strip().lower() == "busy" for event in events):
        _log("Les flux ICS retournent uniquement 'Busy'. Utilise les liens ICS prives (adresse secrete) ou rends les details des evenements publics dans Google Agenda.", always=True)
    elif had_error:
        _log("Certains calendriers n'ont pas pu etre recuperes.", always=True)

    events = sorted(events, key=lambda x: x["start"])
    today_events = [event for event in events if event["start"].date() == today]
    return today_events


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
        capitalize_first(format_date_fr(datetime.now())),
        f"{weather['today_min']}° / {weather['today_max']}°",
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
        trend = weather.get("today_trend") or []
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

    # Keep only today's events and render them in the left half.
    today = datetime.now().date()
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
        y_left = draw_calendar_event_row(draw, y_left, event, font_tiny, left_x, left_x + col_w)

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
    paste_bear_illustration(img, bear_path, right_box, margin=0)

    img.save(target_path)
    return target_path


def generate_image(output_path=None):
    weather = get_weather()
    events = get_events()
    return render_image(weather, events, output_path=output_path)
