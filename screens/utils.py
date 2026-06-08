import os
from io import BytesIO

import cairosvg
from PIL import Image, ImageFont

import frame


SVG_DIR = os.path.join(frame.SCRIPT_DIR, "svg")
FONTS_DIR = os.path.join(frame.SCRIPT_DIR, "fonts")
DEFAULT_CALENDAR_EVENT_COLORS = ["#F2C94C", "#9B51E0"]


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


def draw_calendar_event_row(draw, y, event, font_tiny, start_x, max_x, calendar_event_colors):
    accent = calendar_event_colors[event["source_index"] % len(calendar_event_colors)]
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


def draw_overflow_ellipsis(draw, event_row_y, start_x, font_tiny):
    pill_w = 72
    ellipsis = "..."
    bbox = draw.textbbox((0, 0), ellipsis, font=font_tiny)
    text_w = bbox[2] - bbox[0]
    text_x = int(round(start_x + (pill_w - text_w) / 2))
    text_y = event_row_y + 22
    draw.text((text_x, text_y), ellipsis, fill="black", font=font_tiny)


def paste_image_fit_box(base_image, image_path, box, margin=10):
    if not image_path or not os.path.exists(image_path):
        return

    x1, y1, x2, y2 = box
    max_w = max(1, int(x2 - x1 - (2 * margin)))
    max_h = max(1, int(y2 - y1 - (2 * margin)))

    with Image.open(image_path) as src:
        image = src.convert("RGBA")
        scale = min(max_w / image.width, max_h / image.height)
        new_w = max(1, int(round(image.width * scale)))
        new_h = max(1, int(round(image.height * scale)))
        resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)

    paste_x = int(round(x1 + ((x2 - x1 - new_w) / 2)))
    paste_y = int(round(y1 + ((y2 - y1 - new_h) / 2)))
    base_image.paste(resized, (paste_x, paste_y), resized)
