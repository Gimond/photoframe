from __future__ import annotations

from typing import Optional, Protocol


class ScreenModule(Protocol):
    def generate_image(self, output_path: Optional[str] = None) -> str:
        """Generate a screen image and return the absolute output path."""


def validate_screen_module(module: object) -> None:
    generate = getattr(module, "generate_image", None)
    if not callable(generate):
        name = getattr(module, "__name__", repr(module))
        raise TypeError(
            f"Le module d'ecran {name} doit exposer une fonction generate_image(output_path=None)."
        )
