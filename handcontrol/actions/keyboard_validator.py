"""Keyboard shortcuts and virtual keys validator (Section 7).

Validates shortcut combinations against an allowed list of keys and explicitly
blocks dangerous system combinations (such as Ctrl+Alt+Del or Win+L).

Independent of UI, OS APIs, and Windows ctypes calls.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Windows Virtual-Key code definitions (VK_*)
# ---------------------------------------------------------------------------

VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # ALT key
VK_PAUSE = 0x13
VK_CAPITAL = 0x14
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PRIOR = 0x21  # PAGE UP
VK_NEXT = 0x22  # PAGE DOWN
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_INSERT = 0x2D
VK_DELETE = 0x2E
VK_LWIN = 0x5B
VK_RWIN = 0x5C

# Multimedia keys
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xCD

# ---------------------------------------------------------------------------
# Allowed key names lookup table
# ---------------------------------------------------------------------------

KEY_MAP: dict[str, int] = {
    # Modifiers
    "ctrl": VK_CONTROL,
    "control": VK_CONTROL,
    "alt": VK_MENU,
    "shift": VK_SHIFT,
    "win": VK_LWIN,
    "windows": VK_LWIN,
    "super": VK_LWIN,
    # Navigation / editing
    "esc": VK_ESCAPE,
    "escape": VK_ESCAPE,
    "tab": VK_TAB,
    "enter": VK_RETURN,
    "return": VK_RETURN,
    "space": VK_SPACE,
    "backspace": VK_BACK,
    "delete": VK_DELETE,
    "del": VK_DELETE,
    "insert": VK_INSERT,
    "home": VK_HOME,
    "end": VK_END,
    "pageup": VK_PRIOR,
    "pagedown": VK_NEXT,
    "up": VK_UP,
    "down": VK_DOWN,
    "left": VK_LEFT,
    "right": VK_RIGHT,
}

# Add single letters a-z -> 0x41 - 0x5A
for char_code in range(ord("a"), ord("z") + 1):
    char = chr(char_code)
    KEY_MAP[char] = ord(char.upper())

# Add digits 0-9 -> 0x30 - 0x39
for digit_code in range(ord("0"), ord("9") + 1):
    digit = chr(digit_code)
    KEY_MAP[digit] = digit_code

# Add function keys F1-F12 -> 0x70 - 0x7B
for i in range(1, 13):
    KEY_MAP[f"f{i}"] = 0x70 + (i - 1)

# Dangerous combinations explicitly blocked (Section 7)
DANGEROUS_SHORTCUTS: frozenset[str] = frozenset(
    {
        "ctrl+alt+del",
        "ctrl+alt+delete",
        "alt+ctrl+del",
        "alt+ctrl+delete",
        "win+l",
        "ctrl+shift+esc",
        "alt+f4",
    }
)

MODIFIER_KEYS: frozenset[int] = frozenset({VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN})


def normalize_shortcut_string(keys_str: str) -> str:
    """Normalize a shortcut string (e.g. 'Ctrl + Shift + F12' -> 'ctrl+shift+f12')."""
    tokens = [t.strip().lower() for t in re.split(r"[\+\s]+", keys_str.strip()) if t.strip()]
    return "+".join(tokens)


def validate_shortcut(keys_str: str) -> tuple[bool, str, list[int]]:
    """Validate a shortcut combination string.

    Args:
        keys_str: Shortcut string, e.g. "Ctrl+C", "Alt+Tab", "Win+D", "Esc".

    Returns:
        (is_valid, error_message, list_of_vk_codes).
        If is_valid is True, error_message is empty and list_of_vk_codes contains
        the virtual key codes to press in order (modifiers first, then action key).
    """
    if not keys_str or not keys_str.strip():
        return False, "Shortcut string cannot be empty.", []

    normalized = normalize_shortcut_string(keys_str)

    # Check dangerous combinations
    if normalized in DANGEROUS_SHORTCUTS:
        return (
            False,
            f"Dangerous shortcut '{keys_str}' is blocked by security policy.",
            [],
        )

    tokens = normalized.split("+")
    modifiers: list[int] = []
    regular_keys: list[int] = []

    for token in tokens:
        if token not in KEY_MAP:
            return False, f"Unknown or disallowed key '{token}' in shortcut '{keys_str}'.", []

        vk = KEY_MAP[token]
        if vk in MODIFIER_KEYS:
            if vk not in modifiers:
                modifiers.append(vk)
        else:
            if vk not in regular_keys:
                regular_keys.append(vk)

    if not modifiers and not regular_keys:
        return False, "No valid keys found in shortcut.", []

    # Sequence: modifiers down -> regular key down+up -> modifiers up
    all_keys = modifiers + regular_keys
    return True, "", all_keys
