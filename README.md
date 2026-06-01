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
   │   Capture    │ ─── Treeherder ──▶│  Downloads the PNG   │
   │ (one push)   │   + Taskcluster   │  screenshots locally │
   └──────────────┘                   └───────────┬──────────┘
                                                  │
                                                  ▼
                                       ┌───────────────────────┐
                                       │     Comparison        │
                                       │  pairs + diffs PNGs   │
                                       │  baseline ↔ candidate │
                                       └──────────┬────────────┘
                                                  │
                                                  ▼
                                       ┌───────────────────────┐
                                       │   Results: one row    │
                                       │  per screenshot, with │
                                       │  a status + diff image│
                                       └───────────────────────┘
```

- **Capture** — all screenshots from *one* revision (one CI push), possibly
  spanning multiple platforms (e.g. `linux2404-64`).
- **Comparison** — a diff between two captures: a **baseline** ("before", usually
  the parent revision) and a **candidate** ("after", your change). Produces one
  **Result** per screenshot.

---

## Quick start

Needs **Python 3.9–3.13** (3.14 currently breaks Jinja2; on macOS
`/usr/bin/python3` is a safe 3.9).

```bash
python3 -m venv .venv          # first time only
.venv/bin/pip install -e .
.venv/bin/uvicorn firefox_vrt.app:app
open http://localhost:8000
```

Or `docker compose up`. Data (SQLite DB + downloaded screenshots) lives in
`./data/`.

---

## Walkthrough

1. On the landing page, pick a **Tree** and paste a **Revision**, then **Fetch
   CI screenshots**. You land on the Capture page while it downloads.
2. Repeat for the *other* revision (typically the parent = your baseline).
3. On the candidate's Capture page, use **Compare against a baseline** and
   **Run comparison**.
4. The Comparison page shows one row per screenshot. By default only the
   interesting ones (`differs`, `orphan`, `size-mismatch`) show; filter chips
   reveal the rest. Open any row **side-by-side** for a synced-pan, zoomable
   two-pane view (`+`/`−` zoom, `0` reset, `F` fit).

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
> Pairing also requires matching **sets** and **platforms**: two captures only
> produce useful results if they ran the *same* sets on the *same* platform —
> otherwise almost everything is `orphan`.

---

## Reading the results

Each screenshot in a comparison gets exactly one status:

| Status | Meaning | Shown by default? |
|---|---|---|
| `differs` | Changed beyond the noise threshold. **The signal you care about.** | ✅ Yes |
| `orphan` | Exists on only one side, so it can't be diffed. Usually different sets/platforms. | ✅ Yes |
| `size-mismatch` | Present on both sides but at different dimensions. | ✅ Yes |
| `known noise` | Differed, but matches a pre-recorded noise rule, so it's forgiven (see below). | ❌ Hidden |
| `identical` | No meaningful difference. | ❌ Hidden |

`identical` and `known noise` start hidden to keep the signal high; the filter
chips reveal them.

**How a diff is decided** — for each paired screenshot, the tool compares pixels
by the largest absolute difference across R/G/B, counting a pixel as "changed"
only above a **tolerance of 8/255 (~3.1%)** (ignores anti-aliasing; matches the
old `compare_screenshots` ImageMagick `-fuzz 3% -metric AE`). If **changed ÷
total pixels** exceeds the **0.1% threshold** it's `differs`, else `identical`.
A diff overlay PNG (baseline greyed out, changed pixels in red) shows *where*.

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
wobble is noise; the whole panel moving is a regression). It's hidden behind a
toggle rather than dropped, because the rule is a heuristic — a real regression
could fall inside the noise band, so the row stays auditable.

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

## Screenshot sets & partial captures

**Sets.** Each capture records the `MOZSCREENSHOTS_SETS` its CI job ran (e.g.
`Toolbars,Tabs`), read from Taskcluster at fetch time so the provenance survives
the task's ~4-week expiry — letting you compare a months-old capture against a
fresh one and confirm they ran the same sets. Comparisons can only pair
screenshots from sets *both* captures ran; non-shared sets show up as `orphan`,
and the Comparison page flags a set mismatch.

**Partial / flaky captures.** mozscreenshots is flaky, so VRT does **not**
require the CI job to be green. A `testfailed` run still uploads the screenshots
it captured before dying, so VRT fetches those and labels the capture *partial*.
Configurations after the failure point never ran, so a comparison against a
partial capture shows the missing screenshots as `orphan` rows, not real diffs.
(When a push runs both `M(ss)` and `M-nofis(ss)` on a platform, VRT fetches only
the canonical `M(ss)` run.)

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
  capture, paints deliberate diffs, and compares original vs. clone.
- **`decode_mach_jwt.py`** — diagnoses `mach try` permission errors by decoding
  the cached Auth0 token (does your session carry the `scm_level` claim Lando
  needs?). Run where `mach`'s auth cache lives.
- **`backfill_mozscreenshots_sets.py`** — fills in `MOZSCREENSHOTS_SETS` for
  captures fetched before set-tracking, while their task definitions still exist.

---

## Not in the UI yet

In the data model / backend but not yet surfaced:

- **Triage** — each result can carry a state (`untriaged` / `expected` /
  `regression` / `needs-investigation`) and a note, with a save endpoint, but the
  rows don't render triage controls yet.
- **Authentication** — none; rely on the hosting layer (IAM / VPN).
- **macOS captures** — blocked upstream (Mozilla bug 1554821).
- **Auto-baseline selection** — you always pick the baseline manually.
