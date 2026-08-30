"""Single entry point: `vobb-read <subcommand>`.

`vobb-read run` wraps the whole pipeline (steps 1-5 from the project spec)
behind one command, per step 6 -- fetch the Goodreads shelf, filter to
physical/English books, show you the filtered list to sanity-check, then
check each one against the three target VOEBB branches and write a report.

`vobb-read explore` is the one-time (or "VOEBB changed their site again")
manual calibration step -- see voebb.py's module docstring.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from . import goodreads, openlibrary, report, voebb
from .models import Book, CatalogResult, EnrichedBook

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE_URL = "https://www.voebb.de/aDISWeb/app/prod00"


def _out_dir() -> Path:
    d = ROOT / "out"
    d.mkdir(exist_ok=True)
    return d


def _config(name: str) -> Path:
    return ROOT / "config" / name


# ---------------------------------------------------------------------------
# steps 1-2: fetch + enrich + filter
# ---------------------------------------------------------------------------


def _fetch_and_filter(rss_url: str) -> tuple[list[EnrichedBook], list[EnrichedBook]]:
    print(f"Fetching Goodreads shelf feed...")
    books = goodreads.fetch_shelf(rss_url)
    print(f"  {len(books)} book(s) on the shelf.")

    print("Looking up each ISBN13 on Open Library (language + format)...")
    cache_path = _out_dir() / "cache" / "openlibrary_cache.json"
    enriched = openlibrary.enrich_books(books, cache_path=cache_path)

    kept = [e for e in enriched if e.passes_filter]
    excluded = [e for e in enriched if not e.passes_filter]
    print(f"  {len(kept)} physical/English book(s) kept, {len(excluded)} excluded.")
    return kept, excluded


def _print_filtered_list(kept: list[EnrichedBook]):
    print("\nFiltered list (physical + English only):")
    for e in sorted(kept, key=lambda e: e.book.title.lower()):
        print(f"  - {e.book.title} — {e.book.author}  [ISBN13 {e.book.isbn13}]")
    print()


def cmd_fetch(args):
    load_dotenv()
    rss_url = args.rss_url or os.environ.get("GOODREADS_RSS_URL")
    if not rss_url:
        sys.exit("No Goodreads RSS URL given. Pass --rss-url or set GOODREADS_RSS_URL in .env.")

    kept, excluded = _fetch_and_filter(rss_url)
    _print_filtered_list(kept)

    out = _out_dir() / "filtered.json"
    out.write_text(json.dumps([_enriched_to_json(e) for e in kept], indent=2, ensure_ascii=False))
    excl_out = _out_dir() / "excluded.json"
    excl_out.write_text(json.dumps([_enriched_to_json(e) for e in excluded], indent=2, ensure_ascii=False))
    print(f"Saved filtered list to {out}")


# ---------------------------------------------------------------------------
# step 3: explore / calibrate
# ---------------------------------------------------------------------------


def cmd_explore(args):
    load_dotenv()
    base_url = args.base_url or os.environ.get("VOEBB_BASE_URL", DEFAULT_BASE_URL)
    out_dir = _out_dir() / "explore"
    voebb.explore(
        base_url=base_url,
        selectors_path=_config("selectors.json"),
        out_dir=out_dir,
        isbn13=args.isbn13,
        title=args.title,
        author=args.author,
    )


# ---------------------------------------------------------------------------
# step 4: search VOEBB for the filtered list
# ---------------------------------------------------------------------------


def _run_catalog_check(kept: list[EnrichedBook], base_url: str, headless: bool, delay_min: float, delay_max: float) -> list[CatalogResult]:
    from playwright.sync_api import sync_playwright

    branches = voebb.load_branches(_config("branches.json"))
    selectors = voebb.load_selectors(_config("selectors.json"))

    results: list[CatalogResult] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            for i, enriched in enumerate(sorted(kept, key=lambda e: e.book.title.lower()), start=1):
                book = enriched.book
                print(f"[{i}/{len(kept)}] Searching VOEBB: {book.title} — {book.author}")
                outcome = voebb.search_book(
                    page=page,
                    base_url=base_url,
                    isbn13=book.isbn13,
                    title=book.title,
                    author=book.author,
                    branches=branches,
                    selectors=selectors,
                )
                results.append(
                    CatalogResult(
                        enriched=enriched,
                        found=outcome["found"],
                        matched_by=outcome["matched_by"],
                        holdings=outcome["holdings"],
                        error=outcome["error"],
                    )
                )
                if outcome["error"]:
                    print(f"    ! error: {outcome['error']}")
                elif outcome["holdings"]:
                    branch_names = ", ".join(h.branch_label for h in outcome["holdings"])
                    print(f"    held at: {branch_names}")
                elif outcome["found"]:
                    print("    found in catalog, not at a target branch")
                else:
                    print("    not found in catalog")

                if i < len(kept):
                    delay = random.uniform(delay_min, delay_max)
                    time.sleep(delay)
        finally:
            browser.close()
    return results


def cmd_search(args):
    load_dotenv()
    base_url = args.base_url or os.environ.get("VOEBB_BASE_URL", DEFAULT_BASE_URL)
    delay_min = float(os.environ.get("VOEBB_DELAY_MIN", 2))
    delay_max = float(os.environ.get("VOEBB_DELAY_MAX", 5))

    input_path = Path(args.input) if args.input else _out_dir() / "filtered.json"
    if not input_path.exists():
        sys.exit(f"{input_path} not found -- run `vobb-read fetch` first (or `vobb-read run`).")
    kept = [_enriched_from_json(d) for d in json.loads(input_path.read_text())]

    results = _run_catalog_check(kept, base_url, headless=not args.headed, delay_min=delay_min, delay_max=delay_max)

    out = _out_dir() / "results.json"
    out.write_text(json.dumps([_result_to_json(r) for r in results], indent=2, ensure_ascii=False))
    print(f"Saved catalog results to {out}")


# ---------------------------------------------------------------------------
# step 5: report
# ---------------------------------------------------------------------------


def cmd_report(args):
    input_path = Path(args.input) if args.input else _out_dir() / "results.json"
    if not input_path.exists():
        sys.exit(f"{input_path} not found -- run `vobb-read search` first (or `vobb-read run`).")
    results = [_result_from_json(d) for d in json.loads(input_path.read_text())]

    excluded = []
    excl_path = _out_dir() / "excluded.json"
    if excl_path.exists():
        excluded = [_enriched_from_json(d) for d in json.loads(excl_path.read_text())]

    md = report.generate_markdown(results, excluded=excluded)
    out_path = Path(args.output) if args.output else _out_dir() / "report.md"
    out_path.write_text(md)
    print(f"Report written to {out_path}")


# ---------------------------------------------------------------------------
# step 6: the whole thing, one command
# ---------------------------------------------------------------------------


def cmd_run(args):
    load_dotenv()
    rss_url = args.rss_url or os.environ.get("GOODREADS_RSS_URL")
    if not rss_url:
        sys.exit("No Goodreads RSS URL given. Pass --rss-url or set GOODREADS_RSS_URL in .env.")
    base_url = args.base_url or os.environ.get("VOEBB_BASE_URL", DEFAULT_BASE_URL)
    delay_min = float(os.environ.get("VOEBB_DELAY_MIN", 2))
    delay_max = float(os.environ.get("VOEBB_DELAY_MAX", 5))

    kept, excluded = _fetch_and_filter(rss_url)
    _print_filtered_list(kept)

    if not kept:
        print("Nothing to check against VOEBB -- filtered list is empty.")
        return

    if not args.yes:
        answer = input(f"Continue and check these {len(kept)} book(s) against VOEBB? [y/N] ").strip().lower()
        if answer != "y":
            print("Stopped after the filtered-list sanity check.")
            return

    results = _run_catalog_check(kept, base_url, headless=not args.headed, delay_min=delay_min, delay_max=delay_max)

    md = report.generate_markdown(results, excluded=excluded)
    out_path = _out_dir() / f"report-{datetime.now().strftime('%Y%m%d-%H%M')}.md"
    out_path.write_text(md)
    print(f"\nDone. Report written to {out_path}")


# ---------------------------------------------------------------------------
# (de)serialization helpers -- so `run` steps can be split across invocations
# ---------------------------------------------------------------------------


def _enriched_to_json(e: EnrichedBook) -> dict:
    return {"book": asdict(e.book), **{k: v for k, v in asdict(e).items() if k != "book"}}


def _enriched_from_json(d: dict) -> EnrichedBook:
    book = Book(**d["book"])
    return EnrichedBook(
        book=book,
        language_codes=d.get("language_codes", []),
        physical_format=d.get("physical_format"),
        is_english=d.get("is_english"),
        is_physical=d.get("is_physical"),
        lookup_note=d.get("lookup_note", ""),
    )


def _result_to_json(r: CatalogResult) -> dict:
    return {
        "enriched": _enriched_to_json(r.enriched),
        "found": r.found,
        "matched_by": r.matched_by,
        "holdings": [asdict(h) for h in r.holdings],
        "error": r.error,
    }


def _result_from_json(d: dict) -> CatalogResult:
    from .models import BranchHolding

    return CatalogResult(
        enriched=_enriched_from_json(d["enriched"]),
        found=d.get("found", False),
        matched_by=d.get("matched_by", ""),
        holdings=[BranchHolding(**h) for h in d.get("holdings", [])],
        error=d.get("error", ""),
    )


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vobb-read", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="Fetch the Goodreads shelf and apply the physical/English filter (steps 1-2).")
    p_fetch.add_argument("--rss-url", help="Goodreads shelf RSS URL (overrides GOODREADS_RSS_URL in .env).")
    p_fetch.set_defaults(func=cmd_fetch)

    p_explore = sub.add_parser("explore", help="Manually calibrate VOEBB selectors against the live site (step 3).")
    p_explore.add_argument("--base-url", help="VOEBB search form URL (overrides VOEBB_BASE_URL / the default).")
    p_explore.add_argument("--isbn13", help="Run a test search by ISBN13.")
    p_explore.add_argument("--title", help="Run a test search by title (with --author).")
    p_explore.add_argument("--author", help="Author, used with --title.")
    p_explore.set_defaults(func=cmd_explore)

    p_search = sub.add_parser("search", help="Check the filtered list against VOEBB (step 4).")
    p_search.add_argument("--base-url")
    p_search.add_argument("--input", help="Path to filtered.json (default: out/filtered.json).")
    p_search.add_argument("--headed", action="store_true", help="Show the browser window instead of running headless.")
    p_search.set_defaults(func=cmd_search)

    p_report = sub.add_parser("report", help="Render the Markdown report from saved results (step 5).")
    p_report.add_argument("--input", help="Path to results.json (default: out/results.json).")
    p_report.add_argument("--output", help="Where to write the report (default: out/report.md).")
    p_report.set_defaults(func=cmd_report)

    p_run = sub.add_parser("run", help="Run the whole pipeline: fetch, filter, sanity-check, search, report (step 6).")
    p_run.add_argument("--rss-url", help="Goodreads shelf RSS URL (overrides GOODREADS_RSS_URL in .env).")
    p_run.add_argument("--base-url", help="VOEBB search form URL (overrides VOEBB_BASE_URL / the default).")
    p_run.add_argument("--headed", action="store_true", help="Show the browser window instead of running headless.")
    p_run.add_argument("-y", "--yes", action="store_true", help="Skip the filtered-list confirmation prompt.")
    p_run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
