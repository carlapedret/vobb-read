"""Drive the VOEBB (Berlin public library) aDIS/BMS catalog with Playwright.

*** IMPORTANT: this module has NOT been verified against the live site. ***

The sandbox this code was originally written in has no network egress to
voebb.de at all (org network policy blocks it), so the selectors below are
written defensively -- semantic Playwright locators (labels, roles, visible
text) with graceful fallbacks -- rather than hardcoded IDs guessed blindly.

Before trusting `search_book()` / the `run` command, calibrate it for real:

    vobb-read explore

That opens a headed browser against the live search form, runs one or two
test searches, and dumps a screenshot + an auto-detected list of every input/
button/link on the page into out/explore/ so you (or Claude, next time it has
network access) can fill in config/selectors.json with the real field names.
Once selectors.json has real values, they take priority over the heuristics
below.

VOEBB also invalidates reused/bookmarked result-page URLs ("Bitte klicken Sie
auf 'Neue Sitzung'") -- so every single search here starts from a fresh
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


def _maybe_open_first_result(page, selectors: Selectors, timeout=DEFAULT_TIMEOUT_MS):
    """If we landed on a result list rather than a single record's detail/
    holdings view, open the first result. Detection is heuristic: if the
    page already mentions holdings-ish vocabulary, assume it's a detail page."""
    body_text = page.inner_text("body")
    detail_markers = ["exemplar", "standort", "zweigstelle", "verfügbarkeit", "status"]
    if any(m in body_text.lower() for m in detail_markers):
        return  # already looks like a detail/holdings page

    if selectors.result_link:
        link = page.locator(selectors.result_link).first
    else:
        link = page.locator("a").filter(has_text=re.compile(r".{3,}")).first
    if link and link.count() > 0:
        link.click()
        page.wait_for_load_state("networkidle", timeout=timeout)


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
            _maybe_open_first_result(page, selectors)
            container_text = (
                page.inner_text(selectors.holdings_container) if selectors.holdings_container else page.inner_text("body")
            )
            result["holdings"] = extract_holdings_from_text(container_text, branches)
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
