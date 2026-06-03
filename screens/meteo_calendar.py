import os
from datetime import datetime, timedelta, timezone
from io import BytesIO

import cairosvg
import requests
from PIL import Image, ImageDraw, ImageFont
from icalendar import Calendar
import recurring_ical_events

import frame


OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
CITY = os.getenv("CITY", "Massingy,fr")
GOOGLE_CALENDAR_ICS_URL = os.getenv("GOOGLE_CALENDAR_ICS_URL", "")
GOOGLE_CALENDAR_ICS_URLS = [
    url.strip()
    for url in os.getenv("GOOGLE_CALENDAR_ICS_URLS", GOOGLE_CALENDAR_ICS_URL).split(",")
    if url.strip()
]

SVG_DIR = os.path.join(frame.SCRIPT_DIR, "svg")
FONTS_DIR = os.path.join(frame.SCRIPT_DIR, "fonts")
CALENDAR_EVENT_COLORS = ["#F2C94C", "#9B51E0"]
SIMULATE_LONG_EVENTS = os.getenv("SIMULATE_LONG_EVENTS", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


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


def format_temp(value):
    if isinstance(value, (int, float)):
        return str(round(value))
    return str(value)


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


def build_two_hour_slots(day_points):
    target_hours = list(range(8, 23, 2))
    slots = []
    if not day_points:
        return slots

    for hour in target_hours:
        best_point = min(day_points, key=lambda p: abs(p["dt"].hour - hour))
        slots.append(
            {
                "hour": hour,
                "temp": best_point["temp"],
                "icon": best_point["icon"],
            }
        )
    return slots


def _hour_fraction(dt):
    return dt.hour + (dt.minute / 60.0)


def _interpolate_temp(points, target_hour):
    if not points:
        return None

    if target_hour <= points[0][0]:
        return points[0][1]
    if target_hour >= points[-1][0]:
        return points[-1][1]

    for idx in range(1, len(points)):
        h0, t0 = points[idx - 1]
        h1, t1 = points[idx]
        if h0 <= target_hour <= h1:
            if h1 == h0:
                return t0
            ratio = (target_hour - h0) / (h1 - h0)
            return t0 + ((t1 - t0) * ratio)

    return points[-1][1]


def build_temp_trend(day_points, start_hour=7.5, end_hour=22.5, samples=33):
    if not day_points:
        return []

    known = []
    for p in sorted(day_points, key=lambda item: item["dt"]):
        hour = _hour_fraction(p["dt"])
        temp = p.get("temp")
        if isinstance(temp, (int, float)):
            known.append((hour, float(temp)))

    if not known:
        return []

    if samples < 2:
        samples = 2

    out = []
    span = end_hour - start_hour
    for i in range(samples):
        hour = start_hour + ((i / (samples - 1)) * span)
        temp = _interpolate_temp(known, hour)
        if temp is not None:
            out.append({"hour": hour, "temp": temp})
    return out


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

    today_events = []
    tomorrow_events = []

    simulated_count = 9
    for i in range(simulated_count):
        hour = 8 + i
        today_start = datetime.combine(today_date, datetime.min.time()).replace(hour=hour, minute=30)
        tomorrow_start = datetime.combine(tomorrow_date, datetime.min.time()).replace(hour=hour, minute=45)

        today_events.append(
            {
                "start": today_start,
                "summary": f"{base_titles[i % len(base_titles)]} - segment {i + 1}",
                "source_index": i % 2,
            }
        )
        tomorrow_events.append(
            {
                "start": tomorrow_start,
                "summary": f"{base_titles[(simulated_count - 1 - i) % len(base_titles)]} - suivi detaille {i + 1}",
                "source_index": (i + 1) % 2,
            }
        )

    return today_events, tomorrow_events


def get_weather():
    url = "https://api.openweathermap.org/data/2.5/forecast"
    try:
        response = requests.get(
            url,
            params={
                "q": CITY,
                "appid": OPENWEATHER_API_KEY,
                "units": "metric",
                "lang": "fr",
            },
            timeout=10,
        )
        data = response.json()
        if response.status_code != 200 or "list" not in data:
            message = data.get("message", f"HTTP {response.status_code}")
            raise ValueError(message)

        timezone_offset = data.get("city", {}).get("timezone", 0)
        now_city = datetime.now(timezone.utc) + timedelta(seconds=timezone_offset)
        today = now_city.date()
        tomorrow = today + timedelta(days=1)

        today_points = []
        tomorrow_points = []
        for item in data.get("list", []):
            dt_city = datetime.fromtimestamp(item["dt"], timezone.utc) + timedelta(seconds=timezone_offset)
            temp = item.get("main", {}).get("temp")
            icon = (item.get("weather") or [{}])[0].get("icon")
            if temp is None:
                continue

            point = {"dt": dt_city, "temp": temp, "icon": icon}
            if dt_city.date() == today:
                today_points.append(point)
            elif dt_city.date() == tomorrow:
                tomorrow_points.append(point)

        if not today_points and not tomorrow_points:
            raise ValueError("Aucune prevision disponible pour aujourd'hui/demain")

        if today_points:
            today_min = round(min(p["temp"] for p in today_points))
            today_max = round(max(p["temp"] for p in today_points))
        else:
            today_min = "N/A"
            today_max = "N/A"

        if tomorrow_points:
            tomorrow_min = round(min(p["temp"] for p in tomorrow_points))
            tomorrow_max = round(max(p["temp"] for p in tomorrow_points))
            tomorrow_icon = min(tomorrow_points, key=lambda p: abs(p["dt"].hour - 12))["icon"]
        else:
            tomorrow_min = "N/A"
            tomorrow_max = "N/A"
            tomorrow_icon = None

        return {
            "today_min": today_min,
            "today_max": today_max,
            "hourly": build_two_hour_slots(today_points),
            "today_trend": build_temp_trend(today_points, start_hour=7.5, end_hour=22.5),
            "tomorrow_min": tomorrow_min,
            "tomorrow_max": tomorrow_max,
            "tomorrow_icon": tomorrow_icon,
        }
    except Exception as exc:
        print(f"Meteo indisponible ({exc}), utilisation d'une valeur par defaut.")
        return {
            "today_min": "N/A",
            "today_max": "N/A",
            "hourly": [],
            "today_trend": [],
            "tomorrow_min": "N/A",
            "tomorrow_max": "N/A",
            "tomorrow_icon": None,
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
                    print(f"Evenement ignore ({exc}) : {calendar_url}")

            print(f"Calendrier charge ({added_for_calendar} evenement(s)) : {calendar_url}")
        except Exception as exc:
            had_error = True
            print(f"Calendrier indisponible ({exc}) : {calendar_url}")

    if not had_success:
        print("Aucun calendrier accessible, aucun evenement affiche.")
        return [], []

    if events and all(event["summary"].strip().lower() == "busy" for event in events):
        print("Les flux ICS retournent uniquement 'Busy'. Utilise les liens ICS prives (adresse secrete) ou rends les details des evenements publics dans Google Agenda.")
    elif had_error:
        print("Certains calendriers n'ont pas pu etre recuperes.")

    events = sorted(events, key=lambda x: x["start"])
    today_events = [event for event in events if event["start"].date() == today]
    tomorrow_events = [event for event in events if event["start"].date() == tomorrow]
    return today_events, tomorrow_events


def render_image(weather, events, output_path=None):
    target_path = os.path.abspath(output_path) if output_path else frame.DEFAULT_IMAGE_PATH

    img = Image.new("RGB", (800, 480), color="black")
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"  # disable text antialiasing for crisp e-ink rendering

    font_large = load_font(30, bold=True)
    font_small = load_font(22)
    font_title = load_font(24, bold=True)
    font_tiny = load_font(19, bold=True)

    today_events, tomorrow_events = events

    if SIMULATE_LONG_EVENTS:
        today_events, tomorrow_events = build_simulated_events()

    tomorrow_section_h = 120
    tomorrow_top = img.height - tomorrow_section_h

    card_margin = 10
    card_radius = 12
    today_card = (card_margin, card_margin, img.width - card_margin, tomorrow_top - 16)
    tomorrow_card = (card_margin, tomorrow_top, img.width - card_margin, img.height - card_margin)
    draw.rounded_rectangle(today_card, radius=card_radius, fill="white")
    draw.rounded_rectangle(tomorrow_card, radius=card_radius, fill="white")

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
        weather_box_bottom = y + 110
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
            y_top = weather_box_top + 20
            y_bottom = weather_box_top + 94

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
            paste_svg_icon(img, icon_path, center_x, y - 5, size=56)

            hour_text = f"{slot['hour']:02d}h"
            temp_text = f"{format_temp(slot['temp'])}°C"
            hour_bbox = draw.textbbox((0, 0), hour_text, font=font_tiny)
            temp_bbox = draw.textbbox((0, 0), temp_text, font=font_tiny)
            hour_x = int(round(center_x - (hour_bbox[2] - hour_bbox[0]) / 2))
            temp_x = int(round(center_x - (temp_bbox[2] - temp_bbox[0]) / 2))
            draw.text((hour_x, y + 50), hour_text, fill="black", font=font_tiny)
            draw.text((temp_x, y + 77), temp_text, fill="black", font=font_tiny)

        y += 130
    else:
        draw.text((20, y), "Previsions horaires indisponibles.", fill="black", font=font_small)
        y += 34

    # Bottom-anchored "Demain" block.
    max_y_today = tomorrow_top - 12

    max_today_events = 8
    shown_today = today_events[:max_today_events]
    hidden_today = max(0, len(today_events) - len(shown_today))

    # 2 columns x 4 rows for today's events.
    left_margin = 20
    right_margin = 20
    column_gap = 26
    available_w = img.width - left_margin - right_margin
    col_w = int((available_w - column_gap) / 2)
    left_x = left_margin
    right_x = left_x + col_w + column_gap
    today_y_start = y

    left_column_events = shown_today[:4]
    right_column_events = shown_today[4:8]

    y_left = today_y_start
    for event in left_column_events:
        if y_left + 30 > max_y_today:
            hidden_today += 1
            continue
        y_left = draw_calendar_event_row(draw, y_left, event, font_tiny, left_x, left_x + col_w)

    y_right = today_y_start
    for event in right_column_events:
        if y_right + 30 > max_y_today:
            hidden_today += 1
            continue
        y_right = draw_calendar_event_row(draw, y_right, event, font_tiny, right_x, right_x + col_w)

    if not shown_today and today_y_start + 30 <= max_y_today:
        draw.text((left_x, today_y_start), "Aucun evenement.", fill="black", font=font_small)

    if hidden_today > 0:
        draw_plus_circle_icon(draw, img.width - 24, tomorrow_top - 22, radius=12)

    # Left column: weather icon + title + temperatures.
    left_x = 20
    left_col_w = 230
    icon_size = 76
    icon_center_x = left_x + int(icon_size / 2)
    tomorrow_icon_path = openweather_icon_to_svg(weather["tomorrow_icon"])
    paste_svg_icon(img, tomorrow_icon_path, center_x=icon_center_x, top_y=tomorrow_top + 22, size=icon_size)

    title_x = left_x + icon_size + 18
    draw.text((title_x, tomorrow_top + 22), "Demain", fill="black", font=font_large)
    tomorrow_temps = f"{weather['tomorrow_min']}° / {weather['tomorrow_max']}°"
    draw.text((title_x, tomorrow_top + 62), tomorrow_temps, fill="black", font=font_title)

    # Right column: events list.
    right_x = left_x + left_col_w + 20
    right_max_x = img.width - 45
    max_tomorrow_events = 3
    shown_tomorrow = tomorrow_events[:max_tomorrow_events]
    hidden_tomorrow = max(0, len(tomorrow_events) - len(shown_tomorrow))

    y_events = tomorrow_top + 10
    for event in shown_tomorrow:
        y_events = draw_calendar_event_row(draw, y_events, event, font_tiny, right_x, right_max_x)
    if hidden_tomorrow > 0:
        draw_plus_circle_icon(draw, img.width - 24, img.height - 22, radius=12)
    elif not tomorrow_events:
        draw.text((right_x, y_events), "Aucun evenement.", fill="black", font=font_small)

    img.save(target_path)
    return target_path


def generate_image(output_path=None):
    weather = get_weather()
    events = get_events()
    return render_image(weather, events, output_path=output_path)
