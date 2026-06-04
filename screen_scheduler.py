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
    at: time
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

        at = _parse_time(str(raw_rule.get("at", "")).strip())
        days = _normalize_days(raw_rule.get("days", []))
        rules.append(ScheduledRule(days=days, at=at, screen=screen, order=order))

    return rules


def _rule_run_id(rule, scheduled_dt):
    return f"{scheduled_dt.strftime('%Y-%m-%d')}|{rule.at.strftime('%H:%M')}|{rule.screen}|{rule.order}"


def select_screen_from_schedule(schedule_path, now=None, window_minutes=30, state_path=None):
    current_dt = now or datetime.now()
    schedule_data = load_schedule(schedule_path)
    default_screen = str(schedule_data.get("default", DEFAULT_SCREEN)).strip() or DEFAULT_SCREEN
    rules = build_rules(schedule_data)
    state_file = state_path or (str(Path(schedule_path).with_name(".schedule_state.json")))
    state = _load_state(state_file)
    already_run = set(state.get("executed_runs", []))

    due_candidates = []
    for rule in rules:
        if current_dt.weekday() not in rule.days:
            continue

        scheduled_dt = datetime.combine(current_dt.date(), rule.at)
        age = current_dt - scheduled_dt
        if age < timedelta(0):
            continue
        if age > timedelta(minutes=window_minutes):
            continue

        run_id = _rule_run_id(rule, scheduled_dt)
        if run_id in already_run:
            continue

        due_candidates.append((rule, scheduled_dt, run_id))

    if due_candidates:
        chosen_rule, scheduled_dt, run_id = max(due_candidates, key=lambda item: (item[0].at, item[0].order))
        already_run.add(run_id)
        state["executed_runs"] = sorted(already_run)
        _save_state(state_file, state)

        info = (
            f"{chosen_rule.screen} (regle {chosen_rule.at.strftime('%H:%M')} / "
            f"jour {DAY_LABELS[current_dt.weekday()]})"
        )
        return chosen_rule.screen, info

    info = f"aucune regle due dans les {window_minutes} min; aucune action"
    return None, info