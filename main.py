import argparse
import importlib
from datetime import datetime
import os
from pathlib import Path

import frame
from screen_scheduler import select_screen_from_schedule
from screens.screen_interface import validate_screen_module


SCREEN_REGISTRY = {
    "meteo_calendar": "screens.meteo_calendar_glance",
    "meteo_calendar_glance": "screens.meteo_calendar_glance",
    "meteo_calendar_bear": "screens.meteo_calendar_bear_today",
    "meteo_calendar_bear_today": "screens.meteo_calendar_bear_today",
    "meteo_calendar_bear_tomorrow": "screens.meteo_calendar_bear_tomorrow",
    "tmdb_random_list": "screens.tmdb_random_list",
}


def load_screen_module(screen_name):
    module_path = SCREEN_REGISTRY.get(screen_name)
    if not module_path:
        valid = ", ".join(sorted(SCREEN_REGISTRY))
        raise ValueError(f"Ecran inconnu: {screen_name}. Ecrans disponibles: {valid}")
    module = importlib.import_module(module_path)
    validate_screen_module(module)
    return module


def parse_args():
    parser = argparse.ArgumentParser(description="Genere et envoie un ecran unique vers le cadre photo.")
    parser.add_argument(
        "--screen",
        default=None,
        choices=sorted(SCREEN_REGISTRY.keys()),
        help="Nom de l'ecran a generer. Si absent, le planning automatique est utilise.",
    )
    parser.add_argument(
        "--schedule-file",
        default=None,
        help="Chemin du fichier de planning JSON (par defaut: SCREEN_SCHEDULE_FILE ou schedule.json).",
    )
    parser.add_argument(
        "--save-local",
        action="store_true",
        help="Genere l'image localement sans l'envoyer au cadre.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Chemin de sortie de l'image (optionnel).",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("Demarrage du processus de generation d'ecran.")

    screen_name = args.screen
    schedule_action = "manual"
    if screen_name is None:
        schedule_file = args.schedule_file or os.getenv("SCREEN_SCHEDULE_FILE")
        if not schedule_file:
            schedule_file = str(Path(frame.SCRIPT_DIR) / "schedule.json")
        else:
            schedule_path = Path(schedule_file)
            if not schedule_path.is_absolute():
                schedule_file = str(Path(frame.SCRIPT_DIR) / schedule_path)
        schedule_state_file = os.getenv("SCREEN_SCHEDULE_STATE_FILE")
        if schedule_state_file and not Path(schedule_state_file).is_absolute():
            schedule_state_file = str(Path(frame.SCRIPT_DIR) / schedule_state_file)

        screen_name, schedule_info, schedule_action = select_screen_from_schedule(
            schedule_file,
            now=datetime.now(),
            state_path=schedule_state_file,
        )
        print(f"Planning charge: {schedule_info}")

        if schedule_action == "none":
            return

        if schedule_action == "exit_window":
            config_ok = frame.configure_photoframe_auto_rotate(True)
            rotate_ok = frame.rotate_photoframe()
            if config_ok and rotate_ok:
                print("Sortie de fenetre: auto_rotate reactive et rotation demandee.")
            else:
                print("Sortie de fenetre: echec partiel sur /config ou /rotate.")
            return

        if screen_name is None:
            return

    module = load_screen_module(screen_name)

    output_path = args.output
    if output_path:
        output_path = str(Path(output_path).resolve())

    image_path = module.generate_image(output_path=output_path)

    if args.save_local:
        print(f"Image enregistree localement: {image_path}")
        return

    if schedule_action == "enter_window":
        if not frame.configure_photoframe_auto_rotate(False):
            print("Echec de la configuration auto_rotate=false.")
            return

    if frame.send_to_photoframe(image_path=image_path):
        print("Image envoyee avec succes !")
    else:
        print("Echec de l'envoi.")


if __name__ == "__main__":
    main()
