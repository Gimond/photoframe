import os
from datetime import datetime, timedelta

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
    paste_svg_icon,
)


CITY = os.getenv("CITY", "Massingy,fr")
GOOGLE_CALENDAR_ICS_URL = os.getenv("GOOGLE_CALENDAR_ICS_URL", "")
GOOGLE_CALENDAR_ICS_URLS = [
    url.strip()
    for url in os.getenv("GOOGLE_CALENDAR_ICS_URLS", GOOGLE_CALENDAR_ICS_URL).split(",")
    if url.strip()
]

CALENDAR_EVENT_COLORS = DEFAULT_CALENDAR_EVENT_COLORS
SIMULATE_LONG_EVENTS = os.getenv("SIMULATE_LONG_EVENTS", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


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
    try:
        _, _, today_points, tomorrow_points = fetch_openmeteo_points(CITY)

        if not today_points and not tomorrow_points:
            raise ValueError("Aucune prevision disponible pour aujourd'hui/demain")

        if today_points:
            day_min = round(min(p["temp"] for p in today_points))
            day_max = round(max(p["temp"] for p in today_points))
        else:
            day_min = "N/A"
            day_max = "N/A"

        if tomorrow_points:
            tomorrow_day_min = round(min(p["temp"] for p in tomorrow_points))
            tomorrow_day_max = round(max(p["temp"] for p in tomorrow_points))
            tomorrow_day_icon = min(tomorrow_points, key=lambda p: abs(p["dt"].hour - 12))["icon"]
        else:
            tomorrow_day_min = "N/A"
            tomorrow_day_max = "N/A"
            tomorrow_day_icon = None

        return {
            "day_min": day_min,
            "day_max": day_max,
            "hourly": build_two_hour_slots(today_points),
            "day_trend": build_temp_trend(today_points, start_hour=7.5, end_hour=22.5),
            "tomorrow_day_min": tomorrow_day_min,
            "tomorrow_day_max": tomorrow_day_max,
            "tomorrow_day_icon": tomorrow_day_icon,
        }
    except Exception as exc:
        print(f"Meteo indisponible ({exc}), utilisation d'une valeur par defaut.")
        return {
            "day_min": "N/A",
            "day_max": "N/A",
            "hourly": [],
            "day_trend": [],
            "tomorrow_day_min": "N/A",
            "tomorrow_day_max": "N/A",
            "tomorrow_day_icon": None,
        }


def get_events():
    today_events, tomorrow_events, _, _ = fetch_calendar_events(
        GOOGLE_CALENDAR_ICS_URLS,
        log_fn=lambda message, always=False: print(message),
    )
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
    last_rendered_today = None

    y_left = today_y_start
    for event in left_column_events:
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

    y_right = today_y_start
    for event in right_column_events:
        if y_right + 30 > max_y_today:
            hidden_today += 1
            continue
        last_rendered_today = {"y": y_right, "x": right_x}
        y_right = draw_calendar_event_row(
            draw,
            y_right,
            event,
            font_tiny,
            right_x,
            right_x + col_w,
            CALENDAR_EVENT_COLORS,
        )

    if not shown_today and today_y_start + 30 <= max_y_today:
        draw.text((left_x, today_y_start), "Aucun evenement.", fill="black", font=font_small)

    if hidden_today > 0 and last_rendered_today:
        draw_overflow_ellipsis(draw, last_rendered_today["y"], last_rendered_today["x"], font_tiny)

    # Left column: weather icon + title + temperatures.
    left_x = 20
    left_col_w = 230
    icon_size = 76
    icon_center_x = left_x + int(icon_size / 2)
    tomorrow_icon_path = openweather_icon_to_svg(weather["tomorrow_day_icon"])
    paste_svg_icon(img, tomorrow_icon_path, center_x=icon_center_x, top_y=tomorrow_top + 22, size=icon_size)

    title_x = left_x + icon_size + 18
    draw.text((title_x, tomorrow_top + 22), "Demain", fill="black", font=font_large)
    tomorrow_temps = f"{weather['tomorrow_day_min']}° / {weather['tomorrow_day_max']}°"
    draw.text((title_x, tomorrow_top + 62), tomorrow_temps, fill="black", font=font_title)

    # Right column: events list.
    right_x = left_x + left_col_w + 20
    right_max_x = img.width - 45
    max_tomorrow_events = 3
    shown_tomorrow = tomorrow_events[:max_tomorrow_events]
    hidden_tomorrow = max(0, len(tomorrow_events) - len(shown_tomorrow))
    last_rendered_tomorrow = None

    y_events = tomorrow_top + 10
    for event in shown_tomorrow:
        last_rendered_tomorrow = {"y": y_events, "x": right_x}
        y_events = draw_calendar_event_row(
            draw,
            y_events,
            event,
            font_tiny,
            right_x,
            right_max_x,
            CALENDAR_EVENT_COLORS,
        )
    if hidden_tomorrow > 0:
        draw_overflow_ellipsis(draw, last_rendered_tomorrow["y"], last_rendered_tomorrow["x"], font_tiny)
    elif not tomorrow_events:
        draw.text((right_x, y_events), "Aucun evenement.", fill="black", font=font_small)

    img.save(target_path)
    return target_path


def generate_image(output_path=None):
    weather = get_weather()
    events = get_events()
    return render_image(weather, events, output_path=output_path)
