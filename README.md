# vobb-read

Checks your Goodreads reading list against three specific VÖBB (Berlin public
library) branches, and tells you which books — physical copies, English or
Spanish only — are held there. Wraps the whole thing behind one command.

## Status: working, verified against the live site

Calibrated and run successfully against a real 100-book shelf (2026-09-12).
`vobb_read/voebb.py`'s selectors and holdings-table parsing are based on the
real VÖBB markup, not guesses. If VÖBB changes their site later and searches
start failing or returning nothing, re-calibrate:

```
vobb-read explore --isbn13 <some ISBN13 from your own shelf>
```

This opens a headed browser against the live search form, runs one test
search, and dumps a screenshot + an auto-detected list of every
input/button/link on the page into `out/explore/`. Use that to update the
selectors in `config/selectors.json` (any field left `null` falls back to
the built-in heuristics) and/or `vobb_read/voebb.py` itself.

## What it does

1. **Fetch** — downloads your Goodreads shelf's RSS feed. Goodreads often
   leaves the 13-digit ISBN blank and only supplies the 10-digit one; when
   that happens we compute the ISBN13 ourselves (it's a fixed, standard
   transformation of ISBN10 — not a guess).
2. **Filter** — looks up each book's ISBN13 (falling back to its ISBN10 as
   an alternate lookup key if the ISBN13 alone doesn't resolve — same
   physical edition either way) on the [Open Library Books
   API](https://openlibrary.org/dev/docs/api/books) and keeps only books
   that are English **or** Spanish **and** a physical edition (not
   ebook/Kindle/audiobook). Language and format are never guessed from
   title/publisher — only from Open Library's own data. Anything Open
   Library can't verify is excluded and listed separately in the report,
   not silently dropped. Shows you the filtered list before continuing.
3. **Search** — for each remaining book, searches the live VÖBB catalog
   (fresh visit to the search form every time — VÖBB invalidates reused
   result links) by ISBN13, falling back to title + author. Checks whether
   it's held at any of:
   - Steglitz-Zehlendorf: Ingeborg-Drewitz-Bibliothek
   - Tempelhof-Schöneberg: Bibliothek Schöneberg (aka Theodor-Heuss-Bibliothek —
     both names are matched)
   - ZLB Zentral- und Landesbibliothek (either site: Amerika-Gedenkbibliothek
     or Berliner Stadtbibliothek)

   Waits 2–5 seconds between searches.
4. **Report** — writes a Markdown report: title, author, which target
   branches hold it, and status per branch. Books with no matches are
   listed at the bottom, not omitted.

## Setup

```
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install chromium   # one-time, downloads the browser Playwright drives
cp .env.example .env
```

Edit `.env`:

- `GOODREADS_RSS_URL` — your shelf's private RSS feed URL. Find it on
  Goodreads under **My Books → (a shelf) → RSS** link at the bottom of the
  page. **Keep this private** — `.env` is gitignored and the key must never
  be committed or pasted into a shared/tracked file.
- `VOEBB_BASE_URL` — defaults to the live search form
  (`https://www.voebb.de/aDISWeb/app/prod00`). Never point this at a
  bookmarked results page.

## Usage

Run the whole pipeline (steps 1–5) with one command:

```
vobb-read run
```

It fetches your shelf, filters it, shows you the filtered list, asks you to
confirm before hitting VÖBB, then searches and writes a timestamped report
to `out/report-<timestamp>.md`. Add `--headed` to watch the browser while it
searches (recommended — and **don't close or click into that browser window
while it's running**, it needs to stay open and untouched until the command
finishes), or `-y` to skip the confirmation prompt once you trust it.

You can also run each step on its own, which is useful while calibrating or
if a run gets interrupted partway:

```
vobb-read fetch                 # steps 1-2: fetch + filter, saves out/filtered.json
vobb-read explore --isbn13 ...  # step 3: one-time calibration against the live site
vobb-read search [--headed]     # step 4: check out/filtered.json against VOEBB, saves out/results.json
vobb-read report                # step 5: render out/report.md from out/results.json
```

## Configuration

- `config/branches.json` — the three target branches and every name variant
  the catalog might use for each. Edit this (not the code) if VÖBB renames a
  branch or you want to check different ones.
- `config/selectors.json` — calibrated VÖBB page selectors. Starts out
  all-`null` (falls back to built-in heuristics); filled in with real values
  after the first `explore` run.
- `vobb_read/openlibrary.py`'s `TARGET_LANGUAGES` list — currently
  `["eng", "spa"]`. Edit that list to change which languages pass the filter
  (Open Library's own language codes, e.g. add `"fre"` for French).
- `config/overrides.json` — books you've manually verified are physical +
  English/Spanish but that Open Library has zero data on under either ISBN
  (common for very new releases). Add an entry keyed by ISBN13 with a short
  note, and that book bypasses the Open Library check entirely and goes
  straight into the filtered list every run. Empty by default except for
  one real example.

## Notes

- Open Library lookups are cached in `out/cache/openlibrary_cache.json` so
  re-runs don't re-fetch books you've already looked up. Delete that file
  if you change `TARGET_LANGUAGES` and want previously-excluded books
  re-evaluated (otherwise the cached *edition data* is still fine to
  reuse — only the classification changes — but the simplest fix if you
  want a full re-check is to just delete the cache file).
- This is a personal-shelf checker, not a scraper: one book at a time, with
  a 2–5s delay between VÖBB searches.
- A large fraction of "excluded" books are typically `could not classify
  physical_format=None` or `not found on Open Library` — that's Open
  Library missing data, not proof the book is an ebook or non-English.
  Check the appendix in your report if a book you expected to see isn't
  there.

## Tests

```
pip install -e ".[dev]"  # or just: pip install pytest
pytest
```

Covers the Goodreads RSS parsing (including the ISBN13-from-ISBN10
derivation), the Open Library format/language classification and filtering
(including the ISBN10 fallback lookup), and the branch-name/status-matching
logic used against VÖBB's real holdings-table markup — all against local
fixtures, no network needed. The Playwright driving code itself
(`voebb.py`'s `search_book`) isn't covered by these tests since it needs
the live site; that's what `vobb-read explore` is for.

## Troubleshooting (real issues hit setting this up)

- **"The 'python3' command requires the command line developer tools"** —
  normal on a fresh Mac. Click Install, wait for it to finish (5–15 min),
  then re-check `python3 --version`.
- **`python3 --version` shows something below 3.10** — macOS's built-in
  Python is often older than this tool needs. Install a current one from
  [python.org/downloads/macos](https://www.python.org/downloads/macos/),
  then use its exact path (e.g. `/usr/local/bin/python3`) instead of plain
  `python3` for the `venv` step.
- **A file downloaded from GitHub's web UI doesn't show up in
  `~/Downloads`, or Terminal can't see it** — macOS sometimes needs to be
  told Terminal is allowed to read Downloads (a permission dialog appears
  the first time; click Allow). More reliably, skip the browser entirely
  and pull the raw file straight from GitHub in Terminal:
  ```
  curl -o vobb_read/voebb.py https://raw.githubusercontent.com/carlapedret/vobb-read/<branch>/vobb_read/voebb.py
  ```
  (swap in the actual filename/branch). Verify with `head` or `grep` before
  trusting it landed.
- **Pasting a multi-line file into Terminal goes wrong** (stray `cursh>`
  prompts, `command not found` errors) — this usually means a `cat > file
  << 'EOF' ... EOF` block got split or duplicated in the paste. Prefer the
  `curl` method above for anything more than a couple of lines; it can't
  go wrong the same way.
- **A `run`/`search` errors partway through with `Page.goto: Target page,
  context or browser has been closed`** — the browser window got closed or
  clicked into while the tool was still using it. Let it run completely
  untouched from confirmation (`y`) to the final `Done. Report written to
  ...` line.
- **Every book shows the exact same branches held** — this was a real bug
  (fixed 2026-09-12, see `voebb.py`'s module docstring for the story); if
  you see it again after a VÖBB site change, it means the code is reading
  the wrong page. Re-run `vobb-read explore` and compare against what
  `_read_exemplare_rows` expects.
