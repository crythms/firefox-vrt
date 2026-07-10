"""Chrome-context state actions applied before a capture.

Each action is async privileged JS run in the parent process. All are
feature-detected so the same script survives chrome-API drift between the two
builds being compared.
"""

from __future__ import annotations

_ADD_TABS_JS = """
const resolve = arguments[arguments.length - 1];
const count = arguments[0];
const win = Services.wm.getMostRecentWindow("navigator:browser");
(async () => {
  for (let i = 0; i < count; i++) {
    try { win.gBrowser.addTrustedTab("about:blank"); } catch (e) {}
  }
  await new Promise(r => win.setTimeout(r, 50));
  resolve(true);
})();
"""

_OPEN_SIDEBAR_JS = """
const resolve = arguments[arguments.length - 1];
const commandID = arguments[0];
const win = Services.wm.getMostRecentWindow("navigator:browser");
(async () => {
  try {
    if (win.SidebarController && win.SidebarController.show) {
      await win.SidebarController.show(commandID);
    }
  } catch (e) {}
  resolve(true);
})();
"""

_OPEN_APP_MENU_JS = """
const resolve = arguments[arguments.length - 1];
const win = Services.wm.getMostRecentWindow("navigator:browser");
(async () => {
  try {
    if (win.PanelUI && win.PanelUI.show) { await win.PanelUI.show(); }
  } catch (e) {}
  resolve(true);
})();
"""


def apply_step(capturer, step):
    """Apply one {"action", "args"} step to a live ChromeCapturer session."""
    action = step.get("action")
    args = step.get("args", {})
    if action == "add_tabs":
        capturer.run_chrome(_ADD_TABS_JS, [int(args.get("count", 1))])
    elif action == "open_sidebar":
        capturer.run_chrome(_OPEN_SIDEBAR_JS, [args["commandID"]])
    elif action == "open_app_menu":
        capturer.run_chrome(_OPEN_APP_MENU_JS, [])
    else:
        raise ValueError(f"unknown capture action: {action!r}")


__all__ = ["apply_step"]
