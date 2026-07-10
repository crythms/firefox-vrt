"""Drive a Scenario's ui_states x configs matrix into PNG bytes.

Each (config, ui_state) is captured in a *fresh* browser session so states never
bleed into each other -- determinism over speed. Returns nested dicts the caller
persists (the drawWindow capture *source*, Phase 2) or writes to disk (the CLI).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .actions import apply_step
from .drawwindow import ChromeCapturer
from .scenarios import Scenario


def capture_states(binary, scenario: Scenario, prefs: dict, log_dir: Optional[Path] = None, tag: str = ""):
    """Capture every ui_state under `prefs`, each in a fresh session.

    Returns {ui_state_name: png_bytes}. This is the shared primitive: the config
    axis calls it once per config on one binary; the version axis calls it once
    per binary with a fixed config.
    """
    states: dict[str, bytes] = {}
    for st in scenario.ui_states:
        gecko_log = "-"
        if log_dir is not None:
            gecko_log = str(Path(log_dir) / f"gecko-{tag or 'run'}-{st.name}.log")
        with ChromeCapturer(
            binary,
            prefs=prefs,
            width=scenario.width,
            height=scenario.height,
            gecko_log=gecko_log,
        ) as cap:
            for step in st.steps:
                apply_step(cap, step)
            states[st.name] = cap.capture_png()
    return states


def capture_matrix(binary, scenario: Scenario, log_dir: Optional[Path] = None):
    """Return {config_name: {ui_state_name: png_bytes}} for the whole matrix."""
    return {
        cfg.name: capture_states(binary, scenario, cfg.prefs, log_dir=log_dir, tag=cfg.name)
        for cfg in scenario.configs
    }


def write_matrix(results, out_dir):
    """Write {config: {ui_state: bytes}} to out_dir/<config>/<ui_state>.png."""
    out = Path(out_dir)
    for cfg_name, states in results.items():
        d = out / cfg_name
        d.mkdir(parents=True, exist_ok=True)
        for st_name, png in states.items():
            (d / f"{st_name}.png").write_bytes(png)
    return out


if __name__ == "__main__":
    import glob
    import sys

    from .scenarios import DEFAULT_SCENARIO
    from .spike import diff_stats

    matches = sys.argv[1:] or sorted(
        glob.glob("/Users/cthomas/firefox/obj-*/dist/*.app/Contents/MacOS/firefox")
    )
    if not matches:
        sys.exit("no local build found; pass a firefox binary path")
    binary = matches[-1]

    out = Path(__file__).resolve().parents[2] / "spike_out" / "matrix"
    logs = out / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    print(f"binary: {binary}")
    print(f"out   : {out}\n")

    results = capture_matrix(binary, DEFAULT_SCENARIO, log_dir=logs)
    write_matrix(results, out)

    cfgs = list(results.keys())
    print(f"configs : {cfgs}")
    for st in DEFAULT_SCENARIO.ui_states:
        sizes = {c: len(results[c][st.name]) for c in cfgs}
        print(f"  ui_state {st.name}: bytes={sizes}")

    if len(cfgs) >= 2:
        a, b = cfgs[0], cfgs[1]
        print(f"\nconfig-vs-config diff ({a} vs {b}):")
        for st in DEFAULT_SCENARIO.ui_states:
            print(f"  {st.name}: {diff_stats(results[a][st.name], results[b][st.name])}")
