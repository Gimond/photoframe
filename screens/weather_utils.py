import os
from datetime import datetime, timedelta, timezone

import requests


OPENMETEO_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
OPENMETEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_DEBUG_LOGS = os.getenv("WEATHER_DEBUG_LOGS", "1").strip().lower() in {"1", "true", "yes", "on"}


def _log(message, always=False):
    if always or WEATHER_DEBUG_LOGS:
        print(f"[weather_utils] {message}", flush=True)


def format_temp(value):
    if value is None:
        return "--"
    if isinstance(value, (int, float)):
        return str(round(value))
    return str(value)


def openmeteo_to_openweather_icon(weather_code, is_day):
    code = int(weather_code) if weather_code is not None else -1
    suffix = "d" if int(is_day or 0) == 1 else "n"

    if code == 0:
        prefix = "01"
    elif code in {1, 2}:
        prefix = "02"
    elif code == 3:
        prefix = "04"
    elif code in {45, 48}:
        prefix = "50"
    elif code in {51, 53, 55, 56, 57, 80, 81, 82}:
        prefix = "09"
    elif code in {61, 63, 65, 66, 67}:
        prefix = "10"
    elif code in {71, 73, 75, 77, 85, 86}:
        prefix = "13"
    elif code in {95, 96, 99}:
        prefix = "11"
    else:
        prefix = "01"

    return f"{prefix}{suffix}"


def resolve_city_coordinates(city_name):
    raw = (city_name or "").strip()
    if not raw:
        raise ValueError("Ville vide")

    parts = [part.strip() for part in raw.split(",") if part.strip()]
    base_name = parts[0] if parts else raw
    country_code = None
    if len(parts) >= 2 and len(parts[-1]) == 2 and parts[-1].isalpha():
        country_code = parts[-1].upper()

    queries = []
    if country_code:
        queries.append({"name": base_name, "country_code": country_code})
    queries.append({"name": raw})
    if base_name.lower() != raw.lower():
        queries.append({"name": base_name})

    for query in queries:
        _log(f"Geocoding query: name={query['name']} country={query.get('country_code')}")
        params = {
            "name": query["name"],
            "count": 5,
            "language": "fr",
            "format": "json",
        }
        if query.get("country_code"):
            params["country_code"] = query["country_code"]

        response = requests.get(OPENMETEO_GEOCODING_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        results = data.get("results") or []
        if not results:
            continue

        best = results[0]
        _log(f"Geocoding OK: {best.get('name')} ({best['latitude']}, {best['longitude']})")
        return best["latitude"], best["longitude"]

    raise ValueError(f"Ville introuvable: {city_name}")


def fetch_openmeteo_points(city_name):
    latitude, longitude = resolve_city_coordinates(city_name)
    _log(f"Open-Meteo fetch start: city={city_name}, lat={latitude}, lon={longitude}")
    response = requests.get(
        OPENMETEO_FORECAST_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "temperature_2m,weather_code,is_day",
            "past_days": 1,
            "forecast_days": 2,
            "timezone": "auto",
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    weather_codes = hourly.get("weather_code") or []
    is_days = hourly.get("is_day") or []

    if not (times and temps and weather_codes and is_days):
        raise ValueError("Donnees horaires Open-Meteo incompletes")

    timezone_offset = data.get("utc_offset_seconds", 0)
    now_city = datetime.now(timezone.utc) + timedelta(seconds=timezone_offset)
    today = now_city.date()
    tomorrow = today + timedelta(days=1)

    today_points = []
    tomorrow_points = []
    for dt_text, temp, weather_code, is_day in zip(times, temps, weather_codes, is_days):
        dt_city = datetime.fromisoformat(dt_text)
        if temp is None:
            continue

        icon = openmeteo_to_openweather_icon(weather_code, is_day)
        point = {"dt": dt_city, "temp": temp, "icon": icon}

        if dt_city.date() == today:
            today_points.append(point)
        elif dt_city.date() == tomorrow:
            tomorrow_points.append(point)

    _log(
        f"Open-Meteo fetch OK: hours={len(times)}, today_points={len(today_points)}, "
        f"tomorrow_points={len(tomorrow_points)}"
    )

    return today, tomorrow, today_points, tomorrow_points


def _hour_fraction(dt):
    return dt.hour + (dt.minute / 60.0)


def _interpolate_temp(points, target_hour):
    if not points:
        return None

    if target_hour <= points[0][0]:
        return points[0][1] if target_hour == points[0][0] else None
    if target_hour >= points[-1][0]:
        return points[-1][1] if target_hour == points[-1][0] else None

    for idx in range(1, len(points)):
        h0, t0 = points[idx - 1]
        h1, t1 = points[idx]
        if h0 <= target_hour <= h1:
            if h1 == h0:
                return t0
            ratio = (target_hour - h0) / (h1 - h0)
            return t0 + ((t1 - t0) * ratio)

    return points[-1][1]


def build_two_hour_slots(day_points):
    target_hours = list(range(8, 23, 2))
    slots = []
    if not day_points:
        return slots

    sorted_points = sorted(day_points, key=lambda p: p["dt"])
    known_hours = [(_hour_fraction(p["dt"]), float(p["temp"])) for p in sorted_points]

    for hour in target_hours:
        target_hour = float(hour)
        temp = _interpolate_temp(known_hours, target_hour)
        if temp is None:
            continue

        best_point = min(sorted_points, key=lambda p: abs(_hour_fraction(p["dt"]) - target_hour))
        slots.append(
            {
                "hour": hour,
                "temp": temp,
                "icon": best_point["icon"],
            }
        )
    return slots


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