"""Marionette + drawWindow capture of the Firefox desktop chrome UI.

Drives an arbitrary Firefox binary from outside the build: launches it with a
prepared profile (prefs pin Nimbus/config), switches Marionette to the chrome
context, and reads back the composited chrome window via ``drawWindow`` +
``DRAWWINDOW_USE_WIDGET_LAYERS``. This is in-process capture of exactly what
Gecko paints (tab strip, sidebar, toolbars, XUL panels) -- not an OS screenshot,
so it is deterministic and does not depend on the platform's screencapture tool.

Requires the target build to be launched with a visible window (widget-layer
readback needs a real compositor; a headless window can read back blank).
"""

from __future__ import annotations

import base64

from marionette_driver.marionette import Marionette

# Applied to every capture profile for visual determinism. mozrunner's
# GeckoInstance already disables updates/telemetry/first-run/etc.; these target
# image flakiness specifically.
ANTIFLAKE_PREFS = {
    "layout.css.devPixelsPerPx": "1.0",   # pin DPR -> stable image size + AA
    # Pin deterministic in-process Software WebRender. The GPU subprocess fails to
    # spawn under this local Marionette launch and silently falls back to SW-WR;
    # forcing it removes that non-determinism (and matches reftest practice).
    "gfx.webrender.software": True,
    "layers.gpu-process.enabled": False,
    "ui.caretBlinkTime": -1,              # freeze the text caret
    "ui.prefersReducedMotion": 1,         # kill spinners / transitions
    "browser.startup.page": 0,            # blank startup
    "browser.newtabpage.enabled": False,
    "browser.tabs.warnOnClose": False,
}

# Keep the capture profile out of live Nimbus experiments/rollouts so a
# server-side experiment can't silently enroll and perturb the image (or stomp on
# the sidebar.* prefs we pin). Mirrors testing/profiles/perf/user.js. Note: the
# RemoteSettings loader may still log a benign sync error (it stays enabled via
# the FirefoxLabs policy), but with enrollment off it cannot affect a capture.
EXPERIMENT_ISOLATION_PREFS = {
    "app.shield.optoutstudies.enabled": False,
    "nimbus.rollouts.enabled": False,
    "datareporting.healthreport.uploadEnabled": False,
    "datareporting.policy.dataSubmissionEnabled": False,
}

# Runs once per window (chrome context, system principal). Strips the Marionette
# "remotecontrol" indicator, kills hover/tooltip flake, and fixes the outer size.
_SETUP_JS = """
const [w, h] = arguments;
const win = Services.wm.getMostRecentWindow("navigator:browser");
win.document.getElementById("main-window")?.removeAttribute("remotecontrol");
win.windowUtils.disableNonTestMouseEvents(true);
win.resizeTo(w, h);
"""

# Async: settle the compositor (double rAF + layout flush), then read back the
# whole chrome window. Ported from browser/base/content/test/performance/head.js.
_CAPTURE_JS = """
const resolve = arguments[arguments.length - 1];
const win = Services.wm.getMostRecentWindow("navigator:browser");
(async () => {
  await new Promise(r => win.requestAnimationFrame(() => win.requestAnimationFrame(r)));
  await win.promiseDocumentFlushed(() => {});
  const c = win.document.createElementNS("http://www.w3.org/1999/xhtml", "canvas");
  c.width = win.innerWidth;
  c.height = win.innerHeight;
  const ctx = c.getContext("2d", { alpha: false });
  ctx.drawWindow(win, 0, 0, c.width, c.height, "white",
    ctx.DRAWWINDOW_DRAW_CARET | ctx.DRAWWINDOW_DRAW_VIEW | ctx.DRAWWINDOW_USE_WIDGET_LAYERS);
  resolve(c.toDataURL("image/png"));
})();
"""


class ChromeCapturer:
    """Launch a Firefox binary and capture its chrome window as PNG bytes.

    Use as a context manager; one instance == one browser session with a fixed
    (prefs, viewport). ``prefs`` are merged over ``ANTIFLAKE_PREFS`` and written
    into the launched profile's ``user.js`` -- this is how a config/Nimbus state
    is pinned (most chrome features resolve to a backing pref).
    """

    def __init__(self, binary, prefs=None, width=1280, height=800, gecko_log="-"):
        self.binary = binary
        self.width = width
        self.height = height
        merged = {**ANTIFLAKE_PREFS, **EXPERIMENT_ISOLATION_PREFS}
        if prefs:
            merged.update(prefs)
        self._prefs = merged
        self._gecko_log = gecko_log
        self._m = None

    def __enter__(self):
        # port=0 -> auto-pick a free Marionette port so sessions never collide.
        self._m = Marionette(
            app="fxdesktop",
            bin=self.binary,
            port=0,
            prefs=self._prefs,
            gecko_log=self._gecko_log,
        )
        self._m.start_session()
        self._m.set_context(self._m.CONTEXT_CHROME)
        self._m.execute_script(
            _SETUP_JS, script_args=[self.width, self.height], sandbox="system"
        )
        return self

    def __exit__(self, *exc):
        try:
            if self._m is not None:
                self._m.quit()
        finally:
            self._m = None

    def run_chrome(self, script, args=None):
        """Run an async privileged script in the parent process (chrome context)."""
        return self._m.execute_async_script(
            script, script_args=args or [], sandbox="system", script_timeout=30000
        )

    def capture_png(self):
        """Return the current chrome window as PNG bytes."""
        data_url = self._m.execute_async_script(
            _CAPTURE_JS, sandbox="system", script_timeout=30000
        )
        return base64.b64decode(data_url.split(",", 1)[1])
