"""Semantic colors and dimensions used by all Phase 6 widgets."""
from dataclasses import dataclass


SPACING = {'xs': 4, 'sm': 8, 'md': 12, 'lg': 16, 'xl': 24, '2xl': 32}
RADIUS = {'control': 8, 'card': 12, 'panel': 16}
CONTROL_HEIGHT = 36


@dataclass(frozen=True)
class Palette:
    background: str
    surface: str
    surface_alt: str
    border: str
    text_primary: str
    text_secondary: str
    text_muted: str
    accent: str
    accent_hover: str
    accent_text: str
    success: str
    warning: str
    error: str
    focus: str


LIGHT = Palette(
    background='#F5F7FB', surface='#FFFFFF', surface_alt='#EEF2F8',
    border='#D9E1EC', text_primary='#18263B', text_secondary='#4C5D74',
    text_muted='#68788D', accent='#315EEA', accent_hover='#254BC6',
    accent_text='#FFFFFF', success='#187953', warning='#93621B',
    error='#B73845', focus='#315EEA',
)

DARK = Palette(
    background='#121A29', surface='#1B2638', surface_alt='#243248',
    border='#35455D', text_primary='#F2F5FA', text_secondary='#BECADF',
    text_muted='#94A4BC', accent='#83A2FF', accent_hover='#A1B8FF',
    accent_text='#101827', success='#71D9AB', warning='#F0C581',
    error='#FF8F9A', focus='#A1B8FF',
)
