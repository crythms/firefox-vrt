"""Declarative capture matrix: the ui_states x configs to screenshot.

A `Scenario` expands to one screenshot per (config, ui_state). Mapped onto the
app's data model, one `config` becomes one `Capture` and each `ui_state` is a
`combination` within it -- so a Comparison of two configs (or two builds sharing
a config) diffs ui_state against ui_state through the existing pairing/diff path.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UiState:
    """A named chrome state, reached by applying `steps` (see actions.py)."""

    name: str
    steps: list = field(default_factory=list)  # [{"action": str, "args": {...}}]


@dataclass
class Config:
    """A named config point on the config axis: prefs pinned at launch."""

    name: str
    prefs: dict = field(default_factory=dict)


@dataclass
class Scenario:
    name: str
    ui_states: list
    configs: list
    width: int = 1280
    height: int = 800


# --- built-in configs (config axis) ---
CONFIG_DEFAULT = Config("default", {})
CONFIG_SIDEBAR_REVAMP = Config(
    "sidebar-revamp",
    {
        "sidebar.revamp": True,
        "sidebar.verticalTabs": True,
        "sidebar.visibility": "always-show",
    },
)

# --- built-in ui states ---
# Kept to reliably-drivable states for now; open_app_menu exists in actions.py
# but needs more work to show the panel deterministically, so it's not here yet.
_UI_STATES = [
    UiState("default"),
    UiState("five-tabs", [{"action": "add_tabs", "args": {"count": 4}}]),
    UiState(
        "sidebar-history",
        [{"action": "open_sidebar", "args": {"commandID": "viewHistorySidebar"}}],
    ),
]

DEFAULT_SCENARIO = Scenario(
    name="chrome-basics",
    ui_states=_UI_STATES,
    configs=[CONFIG_DEFAULT, CONFIG_SIDEBAR_REVAMP],
)

__all__ = [
    "CONFIG_DEFAULT",
    "CONFIG_SIDEBAR_REVAMP",
    "Config",
    "DEFAULT_SCENARIO",
    "Scenario",
    "UiState",
]
