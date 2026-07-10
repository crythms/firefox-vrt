"""Phase 0 go/no-go spike for the drawWindow capture approach.

Proves, against a local Firefox build, that we can:
  1. capture the full chrome window deterministically (self-diff == 0), and
  2. pin a Nimbus/pref-gated state (sidebar revamp) that visibly changes capture.

Run:  PYTHONPATH="<repo>" .venv/bin/python -m firefox_vrt.capture.spike [BINARY]
Outputs PNGs + a highlighted diff into <repo>/spike_out/ and prints a verdict.
"""

import glob
import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from firefox_vrt.capture.drawwindow import ChromeCapturer

DEFAULT_BUILD_GLOB = "/Users/cthomas/firefox/obj-*/dist/*.app/Contents/MacOS/firefox"

# The `sidebar` Nimbus feature has no setPref; SidebarManager copies these
# variables into real prefs, so setting them directly pins the state offline.
SIDEBAR_REVAMP_PREFS = {
    "sidebar.revamp": True,
    "sidebar.verticalTabs": True,
    "sidebar.visibility": "always-show",
}

_OPEN_SIDEBAR_JS = """
const resolve = arguments[arguments.length - 1];
const win = Services.wm.getMostRecentWindow("navigator:browser");
(async () => {
  try {
    if (win.SidebarController && win.SidebarController.show) {
      await win.SidebarController.show("viewHistorySidebar");
    }
  } catch (e) {}
  resolve(true);
})();
"""


def _rgb(png_bytes):
    return np.asarray(Image.open(io.BytesIO(png_bytes)).convert("RGB"), dtype=np.int16)


def diff_stats(a, b, threshold=8):
    aa, bb = _rgb(a), _rgb(b)
    if aa.shape != bb.shape:
        return {"shape_mismatch": [list(aa.shape), list(bb.shape)]}
    delta = np.abs(aa - bb).max(axis=2)
    changed = int((delta > threshold).sum())
    total = int(delta.size)
    return {"changed": changed, "total": total, "pct": round(100.0 * changed / total, 4)}


def write_highlight(a, b, out, threshold=8):
    aa, bb = _rgb(a), _rgb(b)
    if aa.shape != bb.shape:
        return
    mask = np.abs(aa - bb).max(axis=2) > threshold
    canvas = (bb * 0.4).astype(np.uint8)  # dim the "after" image
    canvas[mask] = (255, 0, 0)            # paint changed pixels red
    Image.fromarray(canvas).save(out)


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    if argv:
        binary = argv[0]
    else:
        matches = sorted(glob.glob(DEFAULT_BUILD_GLOB))
        if not matches:
            sys.exit(f"no local build found matching {DEFAULT_BUILD_GLOB}")
        binary = matches[-1]

    outdir = Path(__file__).resolve().parents[2] / "spike_out"
    outdir.mkdir(exist_ok=True)
    print(f"binary : {binary}")
    print(f"outdir : {outdir}\n")

    # A) default config, captured twice -> determinism
    with ChromeCapturer(binary, gecko_log=str(outdir / "gecko-a.log")) as cap:
        a1 = cap.capture_png()
        (outdir / "a1_default.png").write_bytes(a1)
        a2 = cap.capture_png()
        (outdir / "a2_default.png").write_bytes(a2)
    self_stats = diff_stats(a1, a2)
    print(f"[determinism]  default self-diff : {self_stats}")

    # B) sidebar-revamp config + open sidebar -> Nimbus/pref pinning is visible
    with ChromeCapturer(binary, prefs=SIDEBAR_REVAMP_PREFS,
                        gecko_log=str(outdir / "gecko-b.log")) as cap:
        cap.run_chrome(_OPEN_SIDEBAR_JS)
        b1 = cap.capture_png()
        (outdir / "b1_sidebar_revamp.png").write_bytes(b1)
    cross = diff_stats(a1, b1)
    write_highlight(a1, b1, outdir / "diff_default_vs_revamp.png")
    print(f"[config axis]  default vs revamp : {cross}\n")

    det_ok = self_stats.get("changed") == 0
    cfg_ok = cross.get("changed", 0) > 0
    print(f"{'PASS' if det_ok else 'CHECK'} determinism (self-diff == 0)")
    print(f"{'PASS' if cfg_ok else 'CHECK'} config axis visible (default != revamp)")
    print(f"\nGO" if (det_ok and cfg_ok) else "\nNEEDS REVIEW")


if __name__ == "__main__":
    main()
