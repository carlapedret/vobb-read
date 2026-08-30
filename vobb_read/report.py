"""Render the final Markdown report."""

from __future__ import annotations

from datetime import datetime

from .models import CatalogResult, EnrichedBook

STATUS_LABEL = {
    "available": "available",
    "on_loan": "on loan",
    "reference_only": "reference only",
    "unknown": "status unclear",
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
        f"Checked **{len(results)}** book(s) (physical, English-language) against: "
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
            "_Dropped by the physical-book / English-language filter, or Open Library "
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
    if e.is_english is False:
        return f"not English (language: {', '.join(e.language_codes) or 'unknown'})"
    if e.is_physical is False:
        return f"not a physical edition (format: {e.physical_format})"
    return "excluded"
