import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path


DAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "lundi": 0,
    "tue": 1,
    "tuesday": 1,
    "mardi": 1,
    "wed": 2,
    "wednesday": 2,
    "mercredi": 2,
    "thu": 3,
    "thursday": 3,
    "jeudi": 3,
    "fri": 4,
    "friday": 4,
    "vendredi": 4,
    "sat": 5,
    "saturday": 5,
    "samedi": 5,
    "sun": 6,
    "sunday": 6,
    "dimanche": 6,
}

DAY_LABELS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

DEFAULT_SCREEN = "meteo_calendar"


@dataclass(frozen=True)
class ScheduledRule:
    days: tuple[int, ...]
    start: time
    end: time
    screen: str
    order: int


def _load_state(state_path):
    path = Path(state_path)
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(data, dict):
        return {}
    return data


def _save_state(state_path, state):
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=True, indent=2)


def _parse_time(value):
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise ValueError(f"Heure invalide '{value}', format attendu HH:MM") from exc


def _normalize_days(values):
    if not values:
        raise ValueError("La regle de planning doit contenir au moins un jour")

    days = []
    for value in values:
        key = str(value).strip().lower()
        if key not in DAY_ALIASES:
            raise ValueError(f"Jour invalide dans le planning: {value}")
        day_index = DAY_ALIASES[key]
        if day_index not in days:
            days.append(day_index)
    return tuple(days)


def load_schedule(schedule_path):
    path = Path(schedule_path)
    if not path.exists():
        return {"default": DEFAULT_SCREEN, "rules": []}

    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    if not isinstance(data, dict):
        raise ValueError("Le fichier de planning doit contenir un objet JSON")

    return data


def build_rules(schedule_data):
    raw_rules = schedule_data.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ValueError("La cle 'rules' doit contenir une liste")

    rules = []
    for order, raw_rule in enumerate(raw_rules):
        if not isinstance(raw_rule, dict):
            raise ValueError("Chaque regle doit etre un objet JSON")

        screen = str(raw_rule.get("screen", "")).strip()
        if not screen:
            raise ValueError("Chaque regle doit definir un 'screen'")

        start = _parse_time(str(raw_rule.get("from", "")).strip())
        end = _parse_time(str(raw_rule.get("to", "")).strip())
        if start == end:
            raise ValueError("Les champs 'from' et 'to' ne peuvent pas etre egaux")

        days = _normalize_days(raw_rule.get("days", []))
        rules.append(ScheduledRule(days=days, start=start, end=end, screen=screen, order=order))

    return rules


def _is_time_in_window(current_time, start_time, end_time):
    if start_time < end_time:
        return start_time <= current_time < end_time
    return current_time >= start_time or current_time < end_time


def _window_start_datetime(current_dt, rule):
    start_date = current_dt.date()
    if rule.start > rule.end and current_dt.time() < rule.end:
        start_date = start_date - timedelta(days=1)
    return datetime.combine(start_date, rule.start)


def _window_id(rule, window_start_dt):
    return f"{window_start_dt.strftime('%Y-%m-%d')}|{rule.start.strftime('%H:%M')}|{rule.end.strftime('%H:%M')}|{rule.screen}|{rule.order}"


def select_screen_from_schedule(schedule_path, now=None, window_minutes=30, state_path=None):
    del window_minutes
    current_dt = now or datetime.now()
    schedule_data = load_schedule(schedule_path)
    rules = build_rules(schedule_data)
    state_file = state_path or (str(Path(schedule_path).with_name(".schedule_state.json")))
    state = _load_state(state_file)
    active_window_id = state.get("active_window_id")

    active_candidates = []
    for rule in rules:
        if not _is_time_in_window(current_dt.time(), rule.start, rule.end):
            continue

        window_start_dt = _window_start_datetime(current_dt, rule)
        if window_start_dt.weekday() not in rule.days:
            continue

        active_candidates.append((rule, window_start_dt))

    if active_candidates:
        chosen_rule, window_start_dt = max(active_candidates, key=lambda item: (item[1], item[0].order))
        current_window_id = _window_id(chosen_rule, window_start_dt)
        if active_window_id == current_window_id:
            info = (
                f"fenetre active deja traitee: {chosen_rule.screen} "
                f"({chosen_rule.start.strftime('%H:%M')}->{chosen_rule.end.strftime('%H:%M')})"
            )
            return None, info, "none"

        state["active_window_id"] = current_window_id
        state["active_screen"] = chosen_rule.screen
        _save_state(state_file, state)

        info = (
            f"entree fenetre: {chosen_rule.screen} "
            f"({chosen_rule.start.strftime('%H:%M')}->{chosen_rule.end.strftime('%H:%M')}, "
            f"jour {DAY_LABELS[window_start_dt.weekday()]})"
        )
        return chosen_rule.screen, info, "enter_window"

    if active_window_id:
        previous_screen = state.get("active_screen") or "inconnu"
        state["active_window_id"] = None
        state["active_screen"] = None
        _save_state(state_file, state)
        info = f"sortie fenetre: {previous_screen}"
        return None, info, "exit_window"

    info = "aucune fenetre active; aucune action"
    return None, info, "none"