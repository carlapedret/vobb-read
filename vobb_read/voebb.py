"""Drive the VOEBB (Berlin public library) aDIS/BMS catalog with Playwright.

Calibrated against the live site on 2026-09-12 via `vobb-read explore`
(manual walkthrough, screenshots reviewed by hand):

- Search form: text input `#Autosuggest`, submit `input[name="$Button"]`.
- A free-text search (title+author, or ISBN13) lands on a "Trefferliste"
  (result list). Each result row has a title link to its full record
  (`a[href*="sp=SAK..."]`); rows that are physical items (not online-only
  e-media) additionally have a "Standort" button whose id is `lrb_<N>_12`,
  where N is the row's 0-indexed position -- same N as that row's SAK link,
  since both are emitted in row order. That id's presence is how we tell a
  physical row from an online-only one without knowing anything else about
  the row's markup.
- Clicking a result's title (not "Standort") opens the full record
  ("Vollanzeige") page, which has an "Exemplarangaben" table listing every
  physical copy: columns Bibliothek | Standort | Signatur |
  Bestellmöglichkeit | Verfügbarkeit. The Bibliothek cell is
  "<Bezirk>: <branch building name>" (e.g. "ZLB: Amerika-Gedenkbibliothek
  (AGB)", "Pankow: Heinrich-Böll-Bibliothek") -- our branch name matching
  is a substring check, so the Bezirk prefix doesn't matter. Verfügbarkeit
  reads e.g. "Verfügbar" or "Ausgeliehen - Fällig am: 28.9.2026".
- The quicker "Standort" popup on the result list itself only breaks
  holdings down by *Bezirk* (borough), not by individual branch -- not
  precise enough for matching a specific branch, so we always click through
  to the full Exemplarangaben table instead.
- Gotcha (found via a real run on a 100-book shelf, then fixed): the result
  *list* page also contains the word "Standort" (it's a button label on
  every row), and separately the site carries a hidden branch-picker
  dropdown listing every VOEBB branch on literally every page. An earlier
  version of this module used a keyword check to decide "are we on the
  detail page yet", which that word tripped, so it never clicked through
  and instead pattern-matched the *whole page* -- meaning it "found" all 3
  target branches in that dropdown for every single book, always. Ground
  truth is now the actual presence of the Exemplarangaben table (see
  _read_exemplare_rows), never a keyword guess on page text.

Everything else below this point (what a "0 results" page says, what the
stale-session interstitial looks like) is still an educated guess -- we
haven't happened to see either live yet. If `vobb-read run` starts
misbehaving on those cases, re-run `vobb-read explore` to see the real page
and update this module / config/selectors.json accordingly.

VOEBB invalidates reused/bookmarked result-page URLs ("Bitte klicken Sie auf
'Neue Sitzung'") -- so every single search here starts from a fresh
page.goto(base_url), never from a stored results link.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .models import BranchHolding

DEFAULT_TIMEOUT_MS = 15_000

# Every result-row title link observed so far matches this pattern
# (?sp=SPROD00&sp=SAK<digits>). Rows are emitted in display order, and a
# physical (non-online-only) row additionally has a "Standort" button whose
# id is lrb_<row index>_12 -- see module docstring.
RESULT_LINK_SELECTOR = 'a[href*="sp=SAK"]'

NO_RESULTS_PATTERNS = [
    r"keine treffer",
    r"0 treffer",
    r"nichts gefunden",
    r"keine ergebnisse",
    r"ihre suche.*ergab keine",
]

NEW_SESSION_PATTERNS = [r"neue sitzung"]

AVAILABLE_KEYWORDS = ["verfügbar", "ausleihbar", "vorhanden", "am standort", "entleihbar", "frei", "bestellbar"]
ON_LOAN_KEYWORDS = ["entliehen", "ausgeliehen", "verliehen", "nicht verfügbar", "vorgemerkt", "zurückerwartet"]
REFERENCE_ONLY_KEYWORDS = ["präsenzbestand", "präsenznutzung", "nicht ausleihbar", "lesesaal", "nur vor ort"]


@dataclass
class BranchConfig:
    id: str
    label: str
    match_names: list[str]


@dataclass
class Selectors:
    """Calibrated selectors, loaded from config/selectors.json. Any field
    left null falls back to the heuristics in this module."""

    search_input: str | None = None
    search_submit: str | None = None
    new_session_button: str | None = None
    holdings_container: str | None = None
    result_link: str | None = None
    no_results_text: list[str] = field(default_factory=lambda: list(NO_RESULTS_PATTERNS))


def load_branches(path: Path) -> list[BranchConfig]:
    data = json.loads(Path(path).read_text())
    return [BranchConfig(id=b["id"], label=b["label"], match_names=b["match_names"]) for b in data["branches"]]


def load_selectors(path: Path) -> Selectors:
    if not Path(path).exists():
        return Selectors()
    data = json.loads(Path(path).read_text())
    sel = Selectors()
    for f in ("search_input", "search_submit", "new_session_button", "holdings_container", "result_link"):
        if data.get(f):
            setattr(sel, f, data[f])
    if data.get("no_results_text"):
        sel.no_results_text = data["no_results_text"]
    return sel


# ---------------------------------------------------------------------------
# Pure, unit-testable text-matching helpers (no Playwright/network needed)
# ---------------------------------------------------------------------------


def match_branches(text: str, branches: list[BranchConfig]) -> list[tuple[BranchConfig, str]]:
    """Return (branch, matched_name) for every branch whose name variant
    appears (case-insensitively) in `text`."""
    hits = []
    low = text.lower()
    for branch in branches:
        for name in branch.match_names:
            if name.lower() in low:
                hits.append((branch, name))
                break
    return hits


def classify_status(text: str) -> str:
    """Classify a snippet of holdings text as available / on_loan /
    reference_only / unknown, using keyword heuristics on the German OPAC
    status wording. Best-effort until calibrated against real output."""
    low = text.lower()
    if any(k in low for k in REFERENCE_ONLY_KEYWORDS):
        return "reference_only"
    if any(k in low for k in ON_LOAN_KEYWORDS):
        return "on_loan"
    if any(k in low for k in AVAILABLE_KEYWORDS):
        return "available"
    return "unknown"


def extract_holdings_from_text(full_text: str, branches: list[BranchConfig]) -> list[BranchHolding]:
    """Scan a block of page text line-by-line for target-branch mentions and
    build a BranchHolding per hit, using a small context window around each
    line to classify availability status.

    This works purely on visible text rather than a specific table structure,
    so it degrades gracefully across markup we haven't seen yet -- but it is
    intentionally a first pass. Once `vobb-read explore` shows the real
    holdings markup, prefer parsing the actual table/row structure instead
    (see holdings_container in config/selectors.json).
    """
    lines = [ln.strip() for ln in full_text.splitlines() if ln.strip()]
    holdings: list[BranchHolding] = []
    for i, line in enumerate(lines):
        hits = match_branches(line, branches)
        if not hits:
            continue
        window = " | ".join(lines[max(0, i - 1) : i + 3])
        status = classify_status(window)
        for branch, matched_name in hits:
            holdings.append(
                BranchHolding(
                    branch_id=branch.id,
                    branch_label=branch.label,
                    matched_name=matched_name,
                    status=status,
                    raw_text=window,
                )
            )
    return holdings


def extract_holdings_from_table_rows(rows: list[tuple[str, str]], branches: list[BranchConfig]) -> list[BranchHolding]:
    """Build BranchHolding entries from (Bibliothek cell, Verfügbarkeit cell)
    pairs read off a book's Exemplarangaben table -- this is the precise,
    calibrated path (see module docstring), used instead of
    extract_holdings_from_text whenever that table is present.
    """
    holdings: list[BranchHolding] = []
    for branch_text, status_text in rows:
        for branch, matched_name in match_branches(branch_text, branches):
            holdings.append(
                BranchHolding(
                    branch_id=branch.id,
                    branch_label=branch.label,
                    matched_name=matched_name,
                    status=classify_status(status_text),
                    raw_text=f"{branch_text} — {status_text}",
                )
            )
    return holdings


def is_no_results(text: str, patterns: list[str]) -> bool:
    low = text.lower()
    return any(re.search(p, low) for p in patterns)


def looks_like_new_session_prompt(text: str) -> bool:
    low = text.lower()
    return any(re.search(p, low) for p in NEW_SESSION_PATTERNS)


# ---------------------------------------------------------------------------
# Playwright-driving code
# ---------------------------------------------------------------------------


def dump_page_summary(page) -> dict:
    """Collect every visible input/button/link on the page -- used by the
    `explore` command to help a human (or a future Claude session with
    network access) quickly identify real selectors."""
    inputs = page.eval_on_selector_all(
        "input, textarea",
        """els => els.map(e => ({
            tag: e.tagName, type: e.type, name: e.name, id: e.id,
            placeholder: e.placeholder, ariaLabel: e.getAttribute('aria-label'),
            visible: !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length)
        }))""",
    )
    buttons = page.eval_on_selector_all(
        "button, input[type=submit], a[role=button]",
        """els => els.map(e => ({
            tag: e.tagName, text: (e.innerText || e.value || '').trim(),
            id: e.id, name: e.name
        }))""",
    )
    links = page.eval_on_selector_all(
        "a[href]",
        """els => els.slice(0, 60).map(e => ({ text: e.innerText.trim(), href: e.href }))""",
    )
    return {"url": page.url, "title": page.title(), "inputs": inputs, "buttons": buttons, "links": links}


def _dismiss_new_session_if_present(page, selectors: Selectors, timeout=3000):
    """VOEBB shows a 'Bitte klicken Sie auf Neue Sitzung' interstitial when a
    stale/reused session URL is hit. We always start from base_url, but this
    is a defensive no-op check in case the form itself lands on that page."""
    try:
        body_text = page.inner_text("body", timeout=timeout)
    except Exception:
        return
    if not looks_like_new_session_prompt(body_text):
        return
    if selectors.new_session_button:
        target = page.locator(selectors.new_session_button)
    else:
        target = page.get_by_role("link", name=re.compile("neue sitzung", re.I))
        if target.count() == 0:
            target = page.get_by_role("button", name=re.compile("neue sitzung", re.I))
    if target.count() > 0:
        target.first.click()
        page.wait_for_load_state("networkidle", timeout=timeout)


def _find_search_input(page, selectors: Selectors):
    if selectors.search_input:
        return page.locator(selectors.search_input).first
    candidates = [
        lambda: page.get_by_label(re.compile("suchbegriff", re.I)),
        lambda: page.get_by_placeholder(re.compile("suchbegriff|suche", re.I)),
        lambda: page.locator('input[type="text"], input[type="search"]').filter(visible=True),
    ]
    for make in candidates:
        loc = make()
        try:
            if loc.count() > 0:
                return loc.first
        except Exception:
            continue
    raise RuntimeError(
        "Could not find the VOEBB search input field. Run `vobb-read explore` "
        "and fill in config/selectors.json with the real field selector."
    )


def _submit_search(page, selectors: Selectors, search_input):
    if selectors.search_submit:
        page.locator(selectors.search_submit).first.click()
        return
    submit_btn = page.get_by_role("button", name=re.compile("suchen|suche starten|start", re.I))
    if submit_btn.count() > 0:
        submit_btn.first.click()
    else:
        search_input.press("Enter")


def _run_one_search(page, base_url: str, query: str, selectors: Selectors, timeout=DEFAULT_TIMEOUT_MS) -> bool:
    """Fresh visit to the search form, fill `query`, submit. Returns True if
    the site appears to show result(s), False if it reports no results."""
    page.goto(base_url, timeout=timeout, wait_until="domcontentloaded")
    _dismiss_new_session_if_present(page, selectors)

    search_input = _find_search_input(page, selectors)
    search_input.fill(query)
    _submit_search(page, selectors, search_input)

    page.wait_for_load_state("networkidle", timeout=timeout)
    body_text = page.inner_text("body")
    if is_no_results(body_text, selectors.no_results_text):
        return False
    return True


def _find_physical_result_link(page):
    """Return the first result-row title link that belongs to a physical
    (non-online-only) item, using the lrb_<row index>_12 "Standort" button
    id as the tell -- see module docstring. None if no row qualifies."""
    links = page.locator(RESULT_LINK_SELECTOR)
    count = links.count()
    for i in range(count):
        if page.locator(f"#lrb_{i}_12").count() > 0:
            return links.nth(i)
    return None


def _click_best_physical_result(page, selectors: Selectors, timeout=DEFAULT_TIMEOUT_MS):
    """Open the best candidate result: the first row that has a "Standort"
    button (i.e. is a physical item, not online-only e-media). Caller is
    responsible for first checking whether we're already on a detail page
    (see search_book) -- this function always tries to click through.

    NOTE: earlier code tried to detect "already on a detail page" from
    keywords like "standort" in the page text, but VOEBB's result *list*
    page also contains that word (it's the button label on every row) --
    that false-positive made every book's holdings come out identical,
    scanning an unrelated branch-picker dropdown that's present on every
    page. Ground truth is the Exemplarangaben table itself (see
    _read_exemplare_rows), not a keyword guess.
    """
    if selectors.result_link:
        link = page.locator(selectors.result_link).first
    else:
        link = _find_physical_result_link(page)
        if link is None:
            # No row exposed a Standort button (e.g. every hit was an
            # online-only edition) -- fall back to the first result at all,
            # so we at least attempt something rather than give up silently.
            fallback = page.locator(RESULT_LINK_SELECTOR).first
            link = fallback if fallback.count() > 0 else None

    if link is not None and link.count() > 0:
        link.click()
        page.wait_for_load_state("networkidle", timeout=timeout)


def _read_exemplare_rows(page) -> list[tuple[str, str]]:
    """Read (Bibliothek, Verfügbarkeit) pairs from the 'Exemplarangaben'
    copies table on a book's full detail page, if one is present. Returns
    [] if no such table is found (caller falls back to text scanning)."""
    table = (
        page.locator("table")
        .filter(has_text=re.compile("Bibliothek", re.I))
        .filter(has_text=re.compile("Verfügbarkeit", re.I))
    )
    if table.count() == 0:
        return []

    rows = table.first.locator("tr")
    pairs: list[tuple[str, str]] = []
    for i in range(rows.count()):
        cells = rows.nth(i).locator("td")
        n = cells.count()
        if n < 2:
            continue  # header row (th, not td) or something malformed
        branch_text = cells.nth(0).inner_text().strip()
        status_text = cells.nth(n - 1).inner_text().strip()
        if branch_text:
            pairs.append((branch_text, status_text))
    return pairs


def search_book(
    page,
    base_url: str,
    isbn13: str | None,
    title: str,
    author: str,
    branches: list[BranchConfig],
    selectors: Selectors,
) -> dict:
    """Search VOEBB for one book: try ISBN13 first, fall back to title+author.

    Returns {"found": bool, "matched_by": str, "holdings": [BranchHolding], "error": str}
    """
    result = {"found": False, "matched_by": "", "holdings": [], "error": ""}
    try:
        if isbn13:
            if _run_one_search(page, base_url, isbn13, selectors):
                result["found"] = True
                result["matched_by"] = "isbn13"

        if not result["found"] and (title or author):
            query = " ".join(p for p in [title, author] if p)
            if _run_one_search(page, base_url, query, selectors):
                result["found"] = True
                result["matched_by"] = "title_author"

        if result["found"]:
            # Ground truth for "are we on a page with real holdings data" is
            # the Exemplarangaben table itself -- not a keyword guess (see
            # _click_best_physical_result's docstring for why that broke).
            table_rows = _read_exemplare_rows(page)
            if not table_rows:
                _click_best_physical_result(page, selectors)
                table_rows = _read_exemplare_rows(page)

            if table_rows:
                result["holdings"] = extract_holdings_from_table_rows(table_rows, branches)
            elif selectors.holdings_container:
                # Only trust a whole-page text scan when a specific
                # container was explicitly calibrated for it -- scanning the
                # *whole* page risks matching unrelated chrome (e.g. VOEBB's
                # branch-picker dropdown, present on every page, lists every
                # branch in Berlin regardless of this book's actual copies).
                result["holdings"] = extract_holdings_from_text(
                    page.inner_text(selectors.holdings_container), branches
                )
            # else: found in the catalog but we couldn't read a holdings
            # table for it -- holdings stays [], which is honest (no
            # branches) rather than risking a false match.
    except Exception as exc:  # noqa: BLE001 - surface any Playwright/site error per-book, don't crash the run
        result["error"] = str(exc)

    return result


def explore(base_url: str, selectors_path: Path, out_dir: Path, isbn13: str | None, title: str | None, author: str | None):
    """Manual calibration helper: open a headed browser, run one test search,
    dump a screenshot + full HTML + an auto-detected field/button/link summary
    so selectors.json can be filled in with real values."""
    from playwright.sync_api import sync_playwright  # imported lazily -- optional dep for most of the CLI

    out_dir.mkdir(parents=True, exist_ok=True)
    selectors = load_selectors(selectors_path)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(base_url, wait_until="domcontentloaded")
        _dismiss_new_session_if_present(page, selectors)

        page.screenshot(path=str(out_dir / "01_search_form.png"), full_page=True)
        (out_dir / "01_search_form.html").write_text(page.content())
        summary = dump_page_summary(page)
        (out_dir / "01_search_form_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        print(f"[explore] search form dumped to {out_dir}")

        query = isbn13 or (f"{title or ''} {author or ''}".strip())
        if query:
            try:
                search_input = _find_search_input(page, selectors)
                search_input.fill(query)
                _submit_search(page, selectors, search_input)
                page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)
            except Exception as exc:  # noqa: BLE001
                print(f"[explore] search attempt failed: {exc}")

            page.screenshot(path=str(out_dir / "02_search_result.png"), full_page=True)
            (out_dir / "02_search_result.html").write_text(page.content())
            summary2 = dump_page_summary(page)
            (out_dir / "02_search_result_summary.json").write_text(json.dumps(summary2, indent=2, ensure_ascii=False))
            print(f"[explore] result page dumped to {out_dir}")
            print(f"[explore] page text preview:\n{page.inner_text('body')[:1500]}")

        print(
            "\n[explore] Browser left open for manual inspection. "
            "Press Enter in this terminal to close it once you've noted the real "
            "selectors, then fill them into config/selectors.json."
        )
        try:
            input()
        except EOFError:
            time.sleep(30)
        browser.close()
