from __future__ import annotations

"""Compatibility CLI for the CAELUS project renderer.

The rendering implementation belongs to ``projects.caelus.renderer``.
This module remains so existing scripts and user commands keep working.
"""

from projects.caelus.renderer import (
    ASSETS_DIR,
    CARD_SIZE,
    COLORS,
    FONT_BOLD,
    FONT_REGULAR,
    LAYOUT,
    ROOT,
    SIGN_NAMES_RU,
    SIGN_ORDER,
    alpha_trim,
    contain,
    draw_centered,
    draw_forecast,
    find_font,
    font,
    load_background,
    load_data,
    main,
    paste_center,
    render_card,
    validate_assets,
    wrap,
)

__all__ = [
    "ASSETS_DIR",
    "CARD_SIZE",
    "COLORS",
    "FONT_BOLD",
    "FONT_REGULAR",
    "LAYOUT",
    "ROOT",
    "SIGN_NAMES_RU",
    "SIGN_ORDER",
    "alpha_trim",
    "contain",
    "draw_centered",
    "draw_forecast",
    "find_font",
    "font",
    "load_background",
    "load_data",
    "main",
    "paste_center",
    "render_card",
    "validate_assets",
    "wrap",
]


if __name__ == "__main__":
    main()
