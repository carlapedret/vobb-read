# vobb-read

Checks your Goodreads reading list against three specific VÖBB (Berlin public
library) branches, and tells you which books — physical copies, English
language only — are held there. Wraps the whole thing behind one command.

## ⚠️ Status: unverified against the live VÖBB site

This tool was written in an environment with **no network access** to
`goodreads.com`, `openlibrary.org`, or `voebb.de` (the sandbox's egress
policy blocks all three). So:

- `vobb_read/goodreads.py` and `vobb_read/openlibrary.py` are unit-tested
  against local fixtures (see `tests/`) and should work as-is.
- `vobb_read/voebb.py` — the part that actually drives the VÖBB catalog —
  is written defensively (semantic Playwright locators with fallbacks, no
  blindly-guessed field IDs) but **has never been run against the real
  site**. You must calibrate it once, for real, before trusting `run` or
  `search`:

  ```
  vobb-read explore --isbn13 9780143127550
  ```

  This opens a headed browser against the live VÖBB search form, runs one
  test search, and dumps a screenshot + an auto-detected list of every
  input/button/link on the page into `out/explore/`. Use that to fill in
  the real selectors in `config/selectors.json` (any field left `null` falls
  back to the built-in heuristics). Re-run `explore` any time VÖBB changes
  their site and searches start failing.

## What it does

1. **Fetch** — downloads your Goodreads shelf's RSS feed.
2. **Filter** — looks up each book's ISBN13 on the [Open Library Books
   API](https://openlibrary.org/dev/docs/api/books) and keeps only books
   that are English-language **and** a physical edition (not ebook/Kindle/
   audiobook). Language and format are never guessed from title/publisher —
   only from Open Library's own data. Anything Open Library can't verify
   is excluded and listed separately in the report, not silently dropped.
   Shows you the filtered list before continuing.
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
searches, or `-y` to skip the confirmation prompt (handy once you trust it).

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
- `config/selectors.json` — calibrated VÖBB page selectors (see the warning
  above). Starts out all-`null`; fill it in after running `explore`.

## Notes

- Open Library lookups are cached in `out/cache/openlibrary_cache.json` so
  re-runs don't re-fetch books you've already looked up.
- This is a personal-shelf checker, not a scraper: one book at a time, with
  a 2–5s delay between VÖBB searches.

## Tests

```
pip install -e ".[dev]"  # or just: pip install pytest
pytest
```

Covers the Goodreads RSS parsing, the Open Library format/language
classification and filtering, and the branch-name/status-matching logic
used against VÖBB's page text — all against local fixtures, no network
needed. The Playwright driving code itself (`voebb.py`'s `search_book`) is
not covered by these tests since it needs the live site; that's what
`vobb-read explore` is for.
