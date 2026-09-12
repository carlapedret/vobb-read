"""Render the final report, as Markdown and as a nicer-looking standalone HTML page."""

from __future__ import annotations

import html as _html
from datetime import datetime

from .models import CatalogResult, EnrichedBook

STATUS_LABEL = {
    "available": "available",
    "on_loan": "on loan",
    "reference_only": "reference only",
    "unknown": "status unclear",
}

# CSS class + a plain-language word for each status, used by the HTML report's
# colored badges.
STATUS_BADGE = {
    "available": ("status-available", "Available now"),
    "on_loan": ("status-onloan", "On loan"),
    "reference_only": ("status-reference", "Reference only"),
    "unknown": ("status-unknown", "Status unclear"),
}


def _branches_cell(result: CatalogResult) -> str:
    if not result.holdings:
        return "—"
    # De-dupe identical (branch, status) pairs (e.g. two lines matched the same copy).
    seen = []
    for h in result.holdings:
        entry = f"{h.branch_label} ({STATUS_LABEL.get(h.status, h.status)})"
        if entry not in seen:
            seen.append(entry)
    return "<br>".join(seen)


def _found_cell(result: CatalogResult) -> str:
    if result.error:
        return f"error: {result.error}"
    if not result.found:
        return "no"
    return f"yes ({result.matched_by})"


def generate_markdown(
    results: list[CatalogResult],
    excluded: list[EnrichedBook] | None = None,
    generated_at: datetime | None = None,
) -> str:
    generated_at = generated_at or datetime.now()
    excluded = excluded or []

    matches = [r for r in results if r.holdings]
    no_matches = [r for r in results if not r.holdings]
    matches.sort(key=lambda r: r.enriched.book.title.lower())
    no_matches.sort(key=lambda r: r.enriched.book.title.lower())

    lines: list[str] = []
    lines.append("# VÖBB Library Check")
    lines.append("")
    lines.append(f"Generated {generated_at.strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append(
        f"Checked **{len(results)}** book(s) (physical, English or Spanish) against: "
        "Ingeborg-Drewitz-Bibliothek, Bibliothek Schöneberg (Theodor-Heuss-Bibliothek), "
        "and ZLB Zentral- und Landesbibliothek."
    )
    lines.append("")
    lines.append(f"- **{len(matches)}** held at a target branch")
    lines.append(f"- **{len(no_matches)}** not held at any target branch")
    if excluded:
        lines.append(f"- **{len(excluded)}** excluded before the catalog check (see appendix)")
    lines.append("")

    lines.append("## Held at a target branch")
    lines.append("")
    if matches:
        lines.append(_table(matches))
    else:
        lines.append("_None of the checked books are currently held at these branches._")
    lines.append("")

    lines.append("## Not held at any target branch")
    lines.append("")
    lines.append(
        "_Includes books not found in the VOEBB catalog at all, and books found but "
        "not at one of the three target branches._"
    )
    lines.append("")
    if no_matches:
        lines.append(_table(no_matches))
    else:
        lines.append("_Every checked book matched at least one target branch._")
    lines.append("")

    if excluded:
        lines.append("## Appendix: excluded before the catalog check")
        lines.append("")
        lines.append(
            "_Dropped by the physical-book / English-or-Spanish filter, or Open Library "
            "had no usable data to verify one of those two things. Listed here so you "
            "can see what was checked and double-check by hand if needed — nothing is "
            "silently dropped._"
        )
        lines.append("")
        lines.append(_excluded_table(excluded))
        lines.append("")

    return "\n".join(lines)


def _table(results: list[CatalogResult]) -> str:
    rows = ["| Title | Author | Branches | Found in catalog |", "|---|---|---|---|"]
    for r in results:
        book = r.enriched.book
        rows.append(f"| {book.title} | {book.author} | {_branches_cell(r)} | {_found_cell(r)} |")
    return "\n".join(rows)


def _excluded_table(excluded: list[EnrichedBook]) -> str:
    rows = ["| Title | Author | Reason |", "|---|---|---|"]
    for e in sorted(excluded, key=lambda e: e.book.title.lower()):
        reason = e.lookup_note or _default_reason(e)
        rows.append(f"| {e.book.title} | {e.book.author} | {reason} |")
    return "\n".join(rows)


def _default_reason(e: EnrichedBook) -> str:
    if e.is_target_language is False:
        return f"not English/Spanish (language: {', '.join(e.language_codes) or 'unknown'})"
    if e.is_physical is False:
        return f"not a physical edition (format: {e.physical_format})"
    return "excluded"


# ---------------------------------------------------------------------------
# HTML report -- a single self-contained page, meant to be opened directly in
# a browser (out/report-<timestamp>.html). No network access needed to view
# it: all CSS/JS is inlined, nothing is fetched from anywhere.
# ---------------------------------------------------------------------------


def _e(text: str) -> str:
    return _html.escape(str(text))


def _badges_html(result: CatalogResult) -> str:
    if not result.holdings:
        return '<span class="muted">—</span>'
    seen = []
    for h in result.holdings:
        css_class, plain = STATUS_BADGE.get(h.status, STATUS_BADGE["unknown"])
        entry = (
            f'<span class="badge {css_class}">{_e(h.branch_label)} '
            f'<span class="badge-status">{_e(plain)}</span></span>'
        )
        if entry not in seen:
            seen.append(entry)
    return "".join(seen)


def _held_rows_html(matches: list[CatalogResult]) -> str:
    rows = []
    for r in matches:
        book = r.enriched.book
        rows.append(
            "<tr>"
            f'<td data-label="Title" class="title-cell">{_e(book.title)}</td>'
            f'<td data-label="Author">{_e(book.author)}</td>'
            f'<td data-label="Held at">{_badges_html(r)}</td>'
            "</tr>"
        )
    return "\n".join(rows)


def _not_held_rows_html(no_matches: list[CatalogResult]) -> str:
    rows = []
    for r in no_matches:
        book = r.enriched.book
        note = "not found in VÖBB catalog" if not r.found else "found, but not at a target branch"
        if r.error:
            note = f"error: {r.error}"
        rows.append(
            "<tr>"
            f'<td data-label="Title" class="title-cell">{_e(book.title)}</td>'
            f'<td data-label="Author">{_e(book.author)}</td>'
            f'<td data-label="Note" class="muted">{_e(note)}</td>'
            "</tr>"
        )
    return "\n".join(rows)


def _excluded_rows_html(excluded: list[EnrichedBook]) -> str:
    rows = []
    for e in sorted(excluded, key=lambda e: e.book.title.lower()):
        reason = e.lookup_note or _default_reason(e)
        rows.append(
            "<tr>"
            f'<td data-label="Title" class="title-cell">{_e(e.book.title)}</td>'
            f'<td data-label="Author">{_e(e.book.author)}</td>'
            f'<td data-label="Reason" class="muted">{_e(reason)}</td>'
            "</tr>"
        )
    return "\n".join(rows)


def generate_html(
    results: list[CatalogResult],
    excluded: list[EnrichedBook] | None = None,
    generated_at: datetime | None = None,
) -> str:
    generated_at = generated_at or datetime.now()
    excluded = excluded or []

    matches = [r for r in results if r.holdings]
    no_matches = [r for r in results if not r.holdings]
    matches.sort(key=lambda r: r.enriched.book.title.lower())
    no_matches.sort(key=lambda r: r.enriched.book.title.lower())

    held_table = (
        (
            '<table class="results" id="held-table"><thead><tr>'
            "<th>Title</th><th>Author</th><th>Held at</th>"
            "</tr></thead><tbody>\n" + _held_rows_html(matches) + "\n</tbody></table>"
        )
        if matches
        else '<p class="empty">None of the checked books are currently held at these branches.</p>'
    )

    not_held_table = (
        (
            '<table class="results" id="not-held-table"><thead><tr>'
            "<th>Title</th><th>Author</th><th>Note</th>"
            "</tr></thead><tbody>\n" + _not_held_rows_html(no_matches) + "\n</tbody></table>"
        )
        if no_matches
        else '<p class="empty">Every checked book matched at least one target branch.</p>'
    )

    excluded_section = ""
    if excluded:
        excluded_section = f"""
    <details class="excluded">
      <summary>Appendix: {len(excluded)} book(s) excluded before the catalog check</summary>
      <p class="muted">
        Dropped by the physical-book / English-or-Spanish filter, or Open Library had no
        usable data to verify one of those two things. Listed here so you can double-check
        by hand if needed &mdash; nothing is silently dropped.
      </p>
      <table class="results">
        <thead><tr><th>Title</th><th>Author</th><th>Reason</th></tr></thead>
        <tbody>
{_excluded_rows_html(excluded)}
        </tbody>
      </table>
    </details>"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VÖBB Library Check</title>
<style>
  :root {{
    --bg: #f7f7f5; --panel: #ffffff; --text: #1a1a1a; --muted: #6b6b6b;
    --border: #e3e1dc; --accent: #7a4f9e;
    --available-bg: #e4f6e8; --available-fg: #1e7a37;
    --onloan-bg: #fdeee0; --onloan-fg: #b2560d;
    --reference-bg: #e6eefb; --reference-fg: #2b5aa3;
    --unknown-bg: #ececec; --unknown-fg: #5a5a5a;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #1b1b1d; --panel: #262628; --text: #f0f0ef; --muted: #a3a3a3;
      --border: #38383a; --accent: #c99bee;
      --available-bg: #16321f; --available-fg: #7fe39b;
      --onloan-bg: #3a2413; --onloan-fg: #f2a45f;
      --reference-bg: #16233a; --reference-fg: #8db4ec;
      --unknown-bg: #333335; --unknown-fg: #c6c6c6;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 24px 16px 60px; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    line-height: 1.5;
  }}
  main {{ max-width: 900px; margin: 0 auto; }}
  h1 {{ margin: 0 0 4px; font-size: 1.6rem; }}
  .subtitle {{ color: var(--muted); margin: 0 0 20px; font-size: 0.95rem; }}
  .stats {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 24px; }}
  .stat {{
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 10px 16px; min-width: 140px;
  }}
  .stat .num {{ font-size: 1.5rem; font-weight: 700; }}
  .stat .label {{ color: var(--muted); font-size: 0.85rem; }}
  h2 {{ font-size: 1.15rem; margin: 28px 0 10px; }}
  input#search {{
    width: 100%; padding: 10px 12px; margin-bottom: 16px; font-size: 1rem;
    border: 1px solid var(--border); border-radius: 8px; background: var(--panel);
    color: var(--text);
  }}
  table.results {{
    width: 100%; border-collapse: collapse; background: var(--panel);
    border: 1px solid var(--border); border-radius: 10px; overflow: hidden;
  }}
  table.results th, table.results td {{
    text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border);
    vertical-align: top;
  }}
  table.results th {{ color: var(--muted); font-weight: 600; font-size: 0.85rem; text-transform: uppercase; }}
  table.results tr:last-child td {{ border-bottom: none; }}
  .title-cell {{ font-weight: 600; }}
  .muted {{ color: var(--muted); }}
  .empty {{ color: var(--muted); font-style: italic; }}
  .badge {{
    display: inline-block; margin: 2px 6px 2px 0; padding: 4px 10px; border-radius: 999px;
    font-size: 0.85rem; white-space: nowrap;
  }}
  .badge-status {{ font-weight: 600; }}
  .status-available {{ background: var(--available-bg); color: var(--available-fg); }}
  .status-onloan {{ background: var(--onloan-bg); color: var(--onloan-fg); }}
  .status-reference {{ background: var(--reference-bg); color: var(--reference-fg); }}
  .status-unknown {{ background: var(--unknown-bg); color: var(--unknown-fg); }}
  details.excluded {{ margin-top: 32px; }}
  details.excluded summary {{
    cursor: pointer; font-weight: 600; padding: 10px 0; color: var(--muted);
  }}
  tr.js-hidden {{ display: none; }}
</style>
</head>
<body>
<main>
  <h1>VÖBB Library Check</h1>
  <p class="subtitle">Generated {generated_at.strftime('%Y-%m-%d %H:%M')}</p>

  <div class="stats">
    <div class="stat"><div class="num">{len(results)}</div><div class="label">books checked</div></div>
    <div class="stat"><div class="num">{len(matches)}</div><div class="label">held at a target branch</div></div>
    <div class="stat"><div class="num">{len(no_matches)}</div><div class="label">not held at a target branch</div></div>
    {f'<div class="stat"><div class="num">{len(excluded)}</div><div class="label">excluded before checking</div></div>' if excluded else ''}
  </div>

  <input id="search" type="search" placeholder="Search by title or author…" autocomplete="off">

  <h2>Held at a target branch</h2>
  {held_table}

  <h2>Not held at any target branch</h2>
  <p class="muted">Includes books not found in the VÖBB catalog at all, and books found but not at one of the three target branches.</p>
  {not_held_table}
  {excluded_section}
</main>
<script>
  (function () {{
    var input = document.getElementById("search");
    if (!input) return;
    var tables = Array.prototype.slice.call(document.querySelectorAll("table.results"));
    input.addEventListener("input", function () {{
      var q = input.value.trim().toLowerCase();
      tables.forEach(function (table) {{
        Array.prototype.forEach.call(table.querySelectorAll("tbody tr"), function (row) {{
          var text = row.textContent.toLowerCase();
          row.classList.toggle("js-hidden", q.length > 0 && text.indexOf(q) === -1);
        }});
      }});
    }});
  }})();
</script>
</body>
</html>
"""
