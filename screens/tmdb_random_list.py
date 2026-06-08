import os
import random
from io import BytesIO

import requests
from PIL import Image, ImageDraw

import frame
from screens.utils import load_font


TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
TMDB_LIST_ID = os.getenv("TMDB_LIST_ID", "")
TMDB_API_BASE_URL = os.getenv("TMDB_API_BASE_URL", "https://api.themoviedb.org/3")
TMDB_IMAGE_BASE_URL = os.getenv("TMDB_IMAGE_BASE_URL", "https://image.tmdb.org/t/p/w342")


def _truncate(text, max_len):
    value = (text or "").strip()
    if len(value) <= max_len:
        return value
    return value[: max_len - 3].rstrip() + "..."


def _fetch_tmdb_movies():
    if not TMDB_API_KEY or not TMDB_LIST_ID:
        return [], "TMDB_API_KEY ou TMDB_LIST_ID manquant dans .env"

    url = f"{TMDB_API_BASE_URL}/list/{TMDB_LIST_ID}"
    try:
        response = requests.get(
            url,
            params={"api_key": TMDB_API_KEY, "language": "fr-FR"},
            timeout=15,
        )
        payload = response.json()
        if response.status_code != 200:
            return [], payload.get("status_message", f"HTTP {response.status_code}")

        items = payload.get("items") or []
        movies = []
        for item in items:
            title = item.get("title") or item.get("name") or "Sans titre"
            poster_path = item.get("poster_path") or ""
            movies.append(
                {
                    "title": title,
                    "poster_path": poster_path,
                }
            )

        if not movies:
            return [], "La liste TMDB est vide"
        return movies, None
    except Exception as exc:
        return [], f"TMDB indisponible: {exc}"


def _pick_three(movies):
    if len(movies) <= 3:
        return movies
    return random.sample(movies, 3)


def _poster_url(poster_path):
    if not poster_path:
        return None
    return f"{TMDB_IMAGE_BASE_URL}{poster_path}"


def _load_poster_image(movie):
    url = _poster_url(movie.get("poster_path"))
    if not url:
        return None
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return Image.open(BytesIO(response.content)).convert("RGB")
    except Exception:
        return None


def render_image(picks, output_path=None, error_message=None):
    target_path = os.path.abspath(output_path) if output_path else frame.DEFAULT_IMAGE_PATH

    img = Image.new("RGB", (800, 480), color="white")
    draw = ImageDraw.Draw(img)

    font_section = load_font(19, bold=True)
    font_body = load_font(20, bold=True)
    font_small = load_font(16)

    if error_message:
        draw.text((20, 120), "Impossible de charger la liste.", fill="black", font=font_section)
        draw.text((20, 155), _truncate(error_message, 95), fill="black", font=font_body)
        img.save(target_path)
        return target_path

    poster_w = 220
    poster_h = 330
    left = 35
    gap = 35
    title_gap = 14

    sample_title_bbox = draw.textbbox((0, 0), "Ag", font=font_body)
    title_h = sample_title_bbox[3] - sample_title_bbox[1]
    block_h = poster_h + title_gap + title_h
    top = max(0, int((img.height - block_h) / 2))

    for idx, movie in enumerate(picks[:3]):
        x = left + idx * (poster_w + gap)
        poster = _load_poster_image(movie)

        if poster is not None:
            fitted = poster.resize((poster_w, poster_h))
            img.paste(fitted, (x, top))
        else:
            draw.rectangle((x, top, x + poster_w, top + poster_h), outline="black", width=2)
            fallback = "Poster indisponible"
            bbox = draw.textbbox((0, 0), fallback, font=font_small)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text(
                (x + (poster_w - tw) / 2, top + (poster_h - th) / 2),
                fallback,
                fill="black",
                font=font_small,
            )

        title = _truncate(movie.get("title", "Sans titre"), 24)
        tb = draw.textbbox((0, 0), title, font=font_body)
        title_w = tb[2] - tb[0]
        title_y = top + poster_h + title_gap
        draw.text((x + (poster_w - title_w) / 2, title_y), title, fill="black", font=font_body)

    img.save(target_path)
    return target_path


def generate_image(output_path=None):
    movies, error = _fetch_tmdb_movies()
    picks = _pick_three(movies)
    return render_image(picks, output_path=output_path, error_message=error)
