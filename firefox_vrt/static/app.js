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

  // --- Theme toggle (auto / light / dark) ------------------------------------
  // The <head> inline script already applied the theme to avoid a flash; here
  // we wire the selector and keep "auto" in sync if the OS theme changes.

  const THEME_KEY = "vrt-theme";
  const darkMql = window.matchMedia("(prefers-color-scheme: dark)");

  function readThemePref() {
    try { return localStorage.getItem(THEME_KEY) || "auto"; } catch (e) { return "auto"; }
  }
  function applyTheme(pref) {
    const dark = pref === "dark" || (pref === "auto" && darkMql.matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
  }
  function initTheme() {
    // Default is "auto" (follow the OS) until the user toggles, after which we
    // store an explicit light/dark choice.
    applyTheme(readThemePref());
    const btn = document.getElementById("theme-toggle");
    if (btn) {
      btn.addEventListener("click", () => {
        const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
        try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
        applyTheme(next);
      });
    }
    // Follow OS changes only while the preference is still "auto".
    const onOsChange = () => { if (readThemePref() === "auto") applyTheme("auto"); };
    if (darkMql.addEventListener) darkMql.addEventListener("change", onOsChange);
    else if (darkMql.addListener) darkMql.addListener(onOsChange);
  }
  document.addEventListener("DOMContentLoaded", initTheme);

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

  // --- Sortable tables -------------------------------------------------------
  // Click a header marked with data-sort to sort the (visible) rows in place.
  // data-sort="text" | "number" | "date". Date columns sort by the <time>
  // element's `datetime` attribute (ISO 8601 sorts chronologically as text).

  function cellKey(cell, type) {
    if (!cell) return "";
    if (type === "date") {
      const t = cell.querySelector("time[datetime]");
      return t ? t.getAttribute("datetime") : cell.textContent.trim();
    }
    if (type === "number") {
      const n = parseFloat(cell.textContent.replace(/[^0-9.\-]/g, ""));
      return isNaN(n) ? 0 : n;
    }
    return cell.textContent.trim().toLowerCase();
  }

  function sortTable(table, colIdx, th, forceDir) {
    const tbody = table.tBodies[0];
    if (!tbody) return;
    const type = th.getAttribute("data-sort") || "text";
    const dir = forceDir
      || (th.getAttribute("aria-sort") === "ascending" ? "descending" : "ascending");
    Array.from(th.parentElement.cells).forEach((h) => h.removeAttribute("aria-sort"));
    th.setAttribute("aria-sort", dir);
    const mult = dir === "ascending" ? 1 : -1;
    const rows = Array.from(tbody.rows);
    rows.sort((a, b) => {
      const av = cellKey(a.cells[colIdx], type);
      const bv = cellKey(b.cells[colIdx], type);
      if (av < bv) return -mult;
      if (av > bv) return mult;
      return 0;
    });
    rows.forEach((r) => tbody.appendChild(r));
  }

  function initSortableTables() {
    document.querySelectorAll("table.sortable").forEach((table) => {
      const head = table.tHead && table.tHead.rows[0];
      if (!head) return;
      Array.from(head.cells).forEach((th, idx) => {
        if (!th.hasAttribute("data-sort")) return;  // skip action columns
        th.classList.add("th-sortable");
        th.addEventListener("click", () => sortTable(table, idx, th));
        // Apply the initial sort for the column marked data-sort-default.
        const def = th.getAttribute("data-sort-default");
        if (def) {
          sortTable(table, idx, th, def === "asc" ? "ascending" : "descending");
        }
      });
    });
  }
  document.addEventListener("DOMContentLoaded", initSortableTables);

  // Diagnostic banners are now grouped under a native <details class="notices">
  // collapsible summary (see templates) — no JS needed to show/hide them.

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
        if (--pending === 0) sbsFit();
      } else {
        img.addEventListener("load", () => {
          if (--pending === 0) sbsFit();
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
