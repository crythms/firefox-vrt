# Firefox VRT — Visual Regression Tool

A small web app for comparing **Firefox chrome screenshots** between two builds.
Paste two revisions; it fetches the screenshots Firefox CI already captures (the
`mochitest-browser-screenshots` / *mozscreenshots* job), diffs them
pixel-by-pixel, and shows you side-by-side which UI screenshots changed and by
how much. Meant as a team-supported replacement for the older
`screenshots.mattn.ca/compare/` workflow.

You don't need a local Firefox build to *use* it — just a revision that has
screenshots in CI. It's for **QA/release testers** eyeballing chrome changes and
**engineers** confirming a chrome change altered only what they expected.

---

## How it works

```
   You paste a revision                  You pick a baseline
          │                                      │
          ▼                                      ▼
   ┌──────────────┐   resolves via    ┌──────────────────────┐
   │   Capture    │ ─── Treeherder ──▶│  Downloads the PNG    │
   │ (one push)   │   + Taskcluster   │  screenshots locally  │
   └──────────────┘                   └──────────┬───────────┘
                                                  │
                                                  ▼
                                       ┌──────────────────────┐
                                       │     Comparison        │
                                       │  pairs + diffs PNGs   │
                                       │  baseline ↔ candidate │
                                       └──────────┬───────────┘
                                                  │
                                                  ▼
                                       ┌──────────────────────┐
                                       │   Results: one row    │
                                       │  per screenshot, with │
                                       │  a status + diff image│
                                       └──────────────────────┘
```

- **Capture** — all screenshots from *one* revision (one CI push), possibly
  spanning multiple platforms (e.g. `linux2404-64`).
- **Comparison** — a diff between two captures: a **baseline** ("before", usually
  the parent revision) and a **candidate** ("after", your change). Produces one
  **Result** per screenshot.

---

## Quick start

Needs **Python 3.9–3.13**. (Python 3.14 currently breaks Jinja2's template
cache; on macOS the system interpreter at `/usr/bin/python3` is a safe 3.9.)

```bash
python3 -m venv .venv          # first time only — use a 3.9–3.13 interpreter
.venv/bin/pip install -e .
.venv/bin/uvicorn firefox_vrt.app:app
open http://localhost:8000
```

Or `docker compose up`. Data (SQLite DB + downloaded screenshots) lives in
`./data/`.

> If you ever rename or move the project folder, the venv's scripts hard-code
> the old path and break — just recreate it (`rm -rf .venv` and repeat the
> steps above).

---

## Walkthrough

1. On the **landing page**, pick a **Tree** and paste a **Revision** (12–40 hex
   chars), then click **Fetch CI screenshots**.
2. You land on the **Capture page** while screenshots download; it auto-refreshes.
   The status badge walks `pending` → `fetching` (blue) → `ready` (green), or
   `failed` (red).
3. Repeat for the *other* revision (typically the parent = your baseline).
4. On the candidate's Capture page, use **Compare against a baseline**: pick the
   baseline from the dropdown and click **Run comparison**.
5. The **Comparison page** shows a summary, filter chips, and one row per
   screenshot. By default only the interesting rows (`differs`, `orphan`,
   `size-mismatch`) are visible.
6. Click **Open side-by-side ↗** on any row to inspect at full zoom with synced
   pan.

> **Which revisions actually have screenshots?** Most don't. The
> `mochitest-browser-screenshots` job is a no-op unless the task was launched
> with `MOZSCREENSHOTS_SETS` set — a normal autoland / mozilla-central push runs
> the job but produces **zero** PNGs. To get one, push to **try** with the env
> var set:
>
> ```bash
> ./mach try fuzzy -q "mochitest-browser-screenshots" \
>   --env "MOZSCREENSHOTS_SETS=Toolbars,Tabs,WindowSize,CustomTitlebar,LightweightThemes,DevTools,AppMenu,Buttons,CustomizeMode,UIDensities,Preferences"
> ```
>
> Paste a revision that captured nothing and the Debug panel shows a clear "0
> PNGs" failure. Also note **pairing requires matching sets and platforms**: two
> captures only produce useful results if they ran the *same* sets on the *same*
> platform — otherwise almost everything is `orphan` and the Comparison page
> warns you.

---

## The pages

### Landing page — `GET /`

- **Compare** form: **Tree** dropdown (`try` / `autoland` / `mozilla-central`,
  defaults to `try`), **Revision** field (12–40 hex), **Fetch CI screenshots**
  button (jumps to the existing capture if you've already fetched this
  revision + tree). Plus an expandable note on the `MOZSCREENSHOTS_SETS` caveat.
- **Recent captures** / **Recent comparisons** tables (last 20 each). Click `#`
  to open; a capture's **Revision** links out to that push on Treeherder.
- Timestamps are stored UTC but rendered in *your* local timezone, 12-hour with
  AM/PM and tz abbreviation (e.g. `May 30, 2026, 11:18 AM PDT`).

### Capture page — `GET /capture/{id}`

State of one revision's screenshot download.

- **Header** — tree, full revision, status badge, fetch time.
- **Progress** — live area (polls every 2 s) while `pending` / `fetching`.
- **Debug details** — expandable (auto-opens on failure): IDs, push ID,
  **screenshot sets**, data dir, status; outbound links (**Treeherder**, **hg
  JSON**, **jobs API**, per-task **Task ID**); and a per-task table (one row per
  platform) with status, **sets**, run number, and `downloaded / total` count.

> **Screenshot sets.** VRT records the `MOZSCREENSHOTS_SETS` each task ran with
> (e.g. `Toolbars,Tabs`), read from the Taskcluster task definition at fetch
> time and stored per task. Task definitions expire (~4 weeks on try), so
> capturing this at fetch time means the provenance survives — letting you
> compare a months-old capture against a fresh one and confirm they ran the
> same sets. Captures fetched before this feature show `(unknown)`; run
> `scripts/backfill_mozscreenshots_sets.py` to fill them in while their task
> definitions still exist.

> When a push runs more than one screenshots variant on a platform — e.g.
> `M(ss)` (Fission, the default) and `M-nofis(ss)` (Fission disabled) — VRT
> fetches **only the canonical `M(ss)` run**, so you get one task per platform
> rather than near-duplicate captures.
- **Compare against a baseline** (once `ready`) — dropdown of ready captures
  (including this one, for a "diff against self" sanity check) + **Run
  comparison**.

### Comparison page — `GET /comparison/{id}`

The main event — the diff between baseline and candidate.

- **Header** (sticky) — both revisions (linked to their captures) + status badge.
- **Progress** while `diffing`; auto-refreshes when done.
- **Summary line** — counts per status (*"N differ · N known noise · …"*).
- **Set comparison** — right under the summary: a green *"✓ same screenshot
  sets"* note when both captures ran the same sets, or a **mismatch banner**
  naming what each side ran and which sets are unique to one (those can't pair,
  so they show up as `orphan` rows). Says *unknown* if either capture predates
  set-tracking.
- **Warning banners** when relevant: *No results* (neither capture had PNGs);
  *Mostly orphans* (≥95% orphaned — likely different sets/platforms, with a
  checklist).
- **Filter chips** — see below.
- **Result rows** (interesting first), each with: platform tag + combination
  name, status badge, diff % and known-noise reason when applicable, **Open
  side-by-side ↗** (when both sides have an image), and three thumbnails
  (**Baseline** / **Candidate** / **Diff** — missing ones show a caption).

### Side-by-side view — `GET /comparison/{id}/side-by-side/{result_id}`

Opens in a new tab. **Baseline** and **Candidate** panes with **synced
scroll/pan** (both panes stay at the same zoom and position) and zoom controls.

---

## Statuses

**Capture / Comparison lifecycle:** `pending` → `fetching`/`diffing` (blue) →
`ready` (green), or `failed` (red, see the failure reason / debug panel).

**Result statuses** — each screenshot in a comparison gets exactly one:

| Badge | Meaning | Shown by default? |
|---|---|---|
| `differs` | Changed beyond the noise threshold. **The signal you care about.** | ✅ Yes |
| `orphan` | Exists on only one side, so it can't be diffed. Usually different sets/platforms. | ✅ Yes |
| `size-mismatch` | Present on both sides but at different dimensions, so a pixel diff isn't meaningful. | ✅ Yes |
| `known noise` | Differed, but matches a pre-recorded noise rule, so it's been forgiven (see below). | ❌ Hidden |
| `identical` | No meaningful difference. | ❌ Hidden |

`identical` and `known noise` start hidden to keep the signal high; reveal them
with the filter chips when you want them.

### Filter chips

On the Comparison page, one chip per status toggles whether those rows show. A
chip reads **"Hide `<status>`"** when visible and **"Show `<status>`"** when
hidden; `identical` and `known noise` start hidden. Filtering is purely visual
and client-side — it never changes the underlying results.

### Side-by-side controls

| Button | Key | Action |
|---|---|---|
| Zoom out (−) | `-` | Zoom out 1.25× |
| 100% (0) | `0` | Reset to actual pixel size |
| Fit (F) | `F` | Scale to fit the pane |
| Zoom in (+) | `+` / `=` | Zoom in 1.25× |

---

## Known noise

Some screenshots differ between runs **even when nobody changed the code** (font
anti-aliasing, drop-shadow blur, sub-pixel jitter). A **known noise** rule says
*"we've seen this flaky diff before — it's noise."* Each rule pins three things
so it catches only the noise, not a real regression:

1. **Platform** — exact match (e.g. `windows7-32`).
2. **Screenshot** — a name pattern (e.g. anything containing `controlcenter`).
3. **Size** — a pixel-count ceiling.

A `differs` result matching all three is demoted to `known noise` and the rule's
**reason** shows on the row. The **pixel ceiling is the safety valve**: if the
same screenshot differs by *more* than allowed, it stays `differs` (a shadow
wobble is noise; the whole panel moving is a regression).

It's shown (behind a toggle) rather than silently dropped because the rule is a
heuristic, not proof — a real regression could fall inside the noise band, so
keeping the row auditable matters, and marking it `identical` would be a lie.

Rules live in `firefox_vrt/data/known_noise.json`; edit it when the team finds a
new noise source:

```json
{
  "platform":   "linux1804-64",
  "name_regex": "(?i).*controlcenter.*",
  "min_diff":   0,
  "max_diff":   5000,
  "reason":     "ControlCenter shadow/blur is non-deterministic on Linux"
}
```

---

## How the diff is computed

For each paired screenshot:

1. Load both as RGB. If **dimensions differ** → `size-mismatch`, no diff image.
2. Per pixel, take the **largest absolute difference across R/G/B**. A pixel is
   "changed" only if that exceeds a **tolerance of 8/255 (~3.1%)**, ignoring
   anti-aliasing noise (matches the old `compare_screenshots` CLI's ImageMagick
   `-fuzz 3% -metric AE`).
3. **Diff % = changed ÷ total pixels.** Above the **0.1% threshold** →
   `differs`, else `identical`. This % is what shows on each row.
4. A **diff overlay PNG** is written: baseline desaturated to grey, changed
   pixels painted solid **red** — easy to scan for *where* the change is.

---

## Configuration

All via environment variables (defaults shown). External services (Treeherder,
Taskcluster, hg.mozilla.org) are public and need no authentication.

| Variable | Default | Purpose |
|---|---|---|
| `DATA_DIR` | `./data` | Where the DB and screenshots are stored. |
| `DATABASE_URL` | `sqlite+aiosqlite:///<DATA_DIR>/firefox-vrt.db` | DB connection (Postgres-swappable). |
| `TASKCLUSTER_ROOT` | `https://firefox-ci-tc.services.mozilla.com` | Taskcluster instance. |
| `TREEHERDER_ROOT` | `https://treeherder.mozilla.org` | Treeherder instance. |
| `HG_ROOT` | `https://hg.mozilla.org` | Mercurial server (parent-revision lookups). |
| `USER_AGENT` | `firefox-vrt/0.1 (...)` | Sent on all outbound requests. |
| `DOWNLOAD_CONCURRENCY` | `5` | Parallel artifact downloads per task. |

---

## Developer scripts

Helpers in `scripts/` for testing without live CI. Run tests with
`.venv/bin/python -m pytest`.

- **`seed_fake_run.py`** — inserts two synthetic captures + a comparison with
  on-disk PNGs exercising every result status. Click through the whole UI
  without CI access.
- **`perturb_capture.py <source_capture_id> [--all-statuses]`** — clones a *real*
  capture, paints deliberate diffs, and compares original vs. clone. See the
  `differs` path on real screenshots without an actual regression.
- **`decode_mach_jwt.py`** — diagnoses `mach try` permission errors by decoding
  the cached Auth0 token and showing whether your session carries the `scm_level`
  group claim Lando needs. Run where `mach`'s auth cache lives.
- **`backfill_mozscreenshots_sets.py`** — fills in `MOZSCREENSHOTS_SETS` for
  captures fetched before set-tracking, by re-reading their Taskcluster task
  definitions (only works while those definitions are still live).

---

## Not in the UI yet

In the data model / backend but not yet surfaced:

- **Triage** — each result can carry a state (`untriaged` / `expected` /
  `regression` / `needs-investigation`) and a note, with a save endpoint, but the
  rows don't render triage controls yet.
- **Authentication** — none; rely on the hosting layer (IAM / VPN).
- **macOS captures** — blocked upstream (Mozilla bug 1554821).
- **Auto-baseline selection** — you always pick the baseline manually.
