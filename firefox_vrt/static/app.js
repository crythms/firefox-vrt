/* Firefox VRT — light client-side glue.
 *
 * Two responsibilities only:
 *   1. Status filter chip toggles (Comparison view).
 *   2. Synced zoom + pan in the side-by-side view.
 *
 * Everything else (progress polling, triage save) is HTMX-driven.
 */

(() => {
  const STATUS_KEYS = ["differs", "identical", "orphan", "size-mismatch", "known-noise"];

  // --- Status filter chips ---------------------------------------------------

  function isHidden(status) {
    return document.body.classList.contains(`hide-${status}`);
  }

  function setChipState(status) {
    const chip = document.querySelector(`.chip[data-status="${status}"]`);
    if (!chip) return;
    chip.setAttribute("aria-pressed", isHidden(status) ? "false" : "true");
    const label = chip.dataset.label || status;
    chip.textContent = (isHidden(status) ? "Show " : "Hide ") + label;
  }

  function toggleStatus(status) {
    document.body.classList.toggle(`hide-${status}`);
    setChipState(status);
  }

  function initChips() {
    STATUS_KEYS.forEach(setChipState);
  }
  document.addEventListener("DOMContentLoaded", initChips);

  // --- Timestamp formatting --------------------------------------------------
  // Server stores a UTC instant (ISO 8601). Render it in the viewer's local
  // zone as a 12-hour clock with AM/PM and a tz abbreviation.

  const TS_FORMAT = {
    year: "numeric", month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit", hour12: true,
    timeZoneName: "short",
  };

  function formatTimestamps(root = document) {
    root.querySelectorAll("time.ts").forEach((el) => {
      if (el.dataset.formatted) return;
      const raw = el.getAttribute("datetime") || el.textContent.trim();
      const d = new Date(raw);
      if (isNaN(d.getTime())) return;  // leave original text on parse failure
      el.textContent = d.toLocaleString(undefined, TS_FORMAT);
      el.dataset.formatted = "1";
    });
  }
  document.addEventListener("DOMContentLoaded", () => formatTimestamps());
  document.body.addEventListener("htmx:afterSwap", (e) => formatTimestamps(e.target));

  // --- Side-by-side synced zoom + pan ----------------------------------------

  const MIN_SCALE = 0.05;
  const MAX_SCALE = 20.0;
  let sbsScale = 1.0;
  let sbsSyncing = false;

  function sbsImages() {
    return Array.from(document.querySelectorAll("[data-sbs-img]"));
  }

  function sbsScrolls() {
    return Array.from(document.querySelectorAll("[data-sbs-scroll]"));
  }

  function applyScale(scale) {
    sbsScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale));
    sbsImages().forEach((img) => {
      if (img.naturalWidth) {
        img.style.width = Math.max(1, img.naturalWidth * sbsScale) + "px";
        img.style.height = "auto";
      }
    });
    const indicator = document.getElementById("sbs-zoom-indicator");
    if (indicator) indicator.textContent = Math.round(sbsScale * 100) + "%";
  }

  function sbsZoom(factor) {
    if (factor === 0) {
      applyScale(1.0);
    } else {
      applyScale(sbsScale * factor);
    }
  }

  function sbsFit() {
    const imgs = sbsImages();
    const scrolls = sbsScrolls();
    if (!imgs.length || !scrolls.length) return;
    let best = 1.0;
    imgs.forEach((img, i) => {
      const scroll = scrolls[i];
      if (!scroll || !img.naturalWidth) return;
      const sx = (scroll.clientWidth - 10) / img.naturalWidth;
      const sy = (scroll.clientHeight - 10) / img.naturalHeight;
      best = Math.min(best, sx, sy);
    });
    applyScale(best);
  }

  function syncScrolls() {
    const scrolls = sbsScrolls();
    if (scrolls.length < 2) return;
    scrolls.forEach((src, idx) => {
      src.addEventListener("scroll", () => {
        if (sbsSyncing) return;
        sbsSyncing = true;
        scrolls.forEach((dst, j) => {
          if (j === idx) return;
          dst.scrollTop = src.scrollTop;
          dst.scrollLeft = src.scrollLeft;
        });
        sbsSyncing = false;
      });
    });
  }

  function initSbs() {
    if (!document.querySelector("[data-sbs-img]")) return;
    // Wait for images to load before computing initial fit.
    const imgs = sbsImages();
    let pending = imgs.length;
    if (pending === 0) return;
    imgs.forEach((img) => {
      if (img.complete) {
        if (--pending === 0) applyScale(1.0);
      } else {
        img.addEventListener("load", () => {
          if (--pending === 0) applyScale(1.0);
        });
      }
    });
    syncScrolls();

    document.addEventListener("keydown", (e) => {
      // Only handle when no input is focused.
      if (document.activeElement && /input|textarea|select/i.test(document.activeElement.tagName)) return;
      switch (e.key) {
        case "+":
        case "=":
          sbsZoom(1.25); break;
        case "-":
        case "_":
          sbsZoom(1 / 1.25); break;
        case "0":
          sbsZoom(0); break;
        case "f":
        case "F":
          sbsFit(); break;
      }
    });
  }
  document.addEventListener("DOMContentLoaded", initSbs);

  window.firefoxVrt = { toggleStatus, sbsZoom, sbsFit };
})();
