from vobb_read.models import BranchHolding
from vobb_read.voebb import (
    BranchConfig,
    _merge_holdings,
    classify_status,
    extract_holdings_from_table_rows,
    extract_holdings_from_text,
    is_no_results,
    match_branches,
)

BRANCHES = [
    BranchConfig(id="steglitz-zehlendorf", label="Ingeborg-Drewitz-Bibliothek", match_names=["Ingeborg-Drewitz-Bibliothek"]),
    BranchConfig(
        id="tempelhof-schoeneberg",
        label="Bibliothek Schöneberg",
        match_names=["Bibliothek Schöneberg", "Theodor-Heuss-Bibliothek"],
    ),
    BranchConfig(
        id="zlb",
        label="ZLB",
        match_names=["Zentral- und Landesbibliothek", "Amerika-Gedenkbibliothek", "Berliner Stadtbibliothek"],
    ),
]


def test_match_branches_direct_name():
    hits = match_branches("Bestand: Ingeborg-Drewitz-Bibliothek, 3 Exemplare", BRANCHES)
    assert len(hits) == 1
    assert hits[0][0].id == "steglitz-zehlendorf"


def test_match_branches_alias_name():
    hits = match_branches("Standort: Theodor-Heuss-Bibliothek", BRANCHES)
    assert len(hits) == 1
    assert hits[0][0].id == "tempelhof-schoeneberg"
    assert hits[0][1] == "Theodor-Heuss-Bibliothek"


def test_match_branches_zlb_either_site():
    assert match_branches("Amerika-Gedenkbibliothek", BRANCHES)[0][0].id == "zlb"
    assert match_branches("Berliner Stadtbibliothek", BRANCHES)[0][0].id == "zlb"


def test_match_branches_no_hit_for_other_branch():
    assert match_branches("Stadtbibliothek Spandau", BRANCHES) == []


def test_classify_status():
    assert classify_status("Status: entliehen bis 12.08.2026") == "on_loan"
    assert classify_status("verfügbar") == "available"
    assert classify_status("nur Präsenznutzung im Lesesaal") == "reference_only"
    assert classify_status("something unrelated") == "unknown"


def test_classify_status_nicht_im_regal():
    # Real Verfügbarkeit text from a live copy (2026-09-12) -- initially
    # misclassified as "unknown" since it doesn't contain "entliehen" etc.
    assert classify_status("Nicht im Regal") == "on_loan"


def test_extract_holdings_from_text():
    text = (
        "Suchergebnis: Piranesi\n"
        "Standort: Ingeborg-Drewitz-Bibliothek\n"
        "Status: verfügbar\n"
        "\n"
        "Standort: Stadtbibliothek Spandau\n"
        "Status: entliehen\n"
        "\n"
        "Standort: Bibliothek Schöneberg\n"
        "Status: entliehen bis 01.09.2026\n"
    )
    holdings = extract_holdings_from_text(text, BRANCHES)
    branch_ids = {h.branch_id for h in holdings}
    assert branch_ids == {"steglitz-zehlendorf", "tempelhof-schoeneberg"}

    by_branch = {h.branch_id: h for h in holdings}
    assert by_branch["steglitz-zehlendorf"].status == "available"
    assert by_branch["tempelhof-schoeneberg"].status == "on_loan"


def test_is_no_results():
    patterns = ["keine treffer", "0 treffer"]
    assert is_no_results("Ihre Suche ergab 0 Treffer.", patterns) is True
    assert is_no_results("Es wurden keine Treffer gefunden.", patterns) is True
    assert is_no_results("1 Treffer gefunden", patterns) is False


# Real Exemplarangaben table content from a live VOEBB search (12 Sep 2026,
# "Piranesi" by Susanna Clarke), reviewed by hand -- see voebb.py's module
# docstring. Rows are (Bibliothek cell, Verfügbarkeit cell).
REAL_EXEMPLARE_ROWS = [
    ("Marzahn-Hellersdorf: Bezirkszentralbibliothek Mark Twain", "Verfügbar"),
    ("Mitte: Bezirkszentralbibliothek Philipp Schaeffer", "Ausgeliehen - Fällig am: 28.9.2026"),
    ("Pankow: Heinrich-Böll-Bibliothek", "Ausgeliehen - Fällig am: 5.10.2026 - Beschädigt / Beschmutzt"),
    ("ZLB: Amerika-Gedenkbibliothek (AGB)", "Ausgeliehen - Fällig am: 28.9.2026"),
    ("ZLB: Amerika-Gedenkbibliothek (AGB)", "Ausgeliehen - Fällig am: 28.9.2026"),
]


def test_extract_holdings_from_table_rows_real_example():
    holdings = extract_holdings_from_table_rows(REAL_EXEMPLARE_ROWS, BRANCHES)
    # Only the two ZLB copies are one of our 3 target branches; the others
    # (Marzahn-Hellersdorf, Mitte, Pankow) correctly produce no holding.
    assert len(holdings) == 2
    assert all(h.branch_id == "zlb" for h in holdings)
    assert all(h.status == "on_loan" for h in holdings)


def test_extract_holdings_from_table_rows_matches_other_target_branches():
    rows = [
        ("Steglitz-Zehlendorf: Ingeborg-Drewitz-Bibliothek", "Verfügbar"),
        ("Tempelhof-Schöneberg: Bibliothek Schöneberg", "Verfügbar"),
        ("Lichtenberg: Stadtteilbibliothek Hellersdorf", "Verfügbar"),  # not a target branch
    ]
    holdings = extract_holdings_from_table_rows(rows, BRANCHES)
    branch_ids = {h.branch_id for h in holdings}
    assert branch_ids == {"steglitz-zehlendorf", "tempelhof-schoeneberg"}
    assert all(h.status == "available" for h in holdings)


# Real Exemplarangaben table for "Astragal" by Albertine Sarrazin (12 Sep
# 2026, a screenshot from a live run) -- 6 physical copies total: 2 at a
# non-target branch, 4 at ZLB (one copy "Nicht im Regal", the status text
# that surfaced the classify_status gap above).
ASTRAGAL_EXEMPLARE_ROWS = [
    ("Charlottenburg-Wilmersdorf: Heinrich-Schulz-Bibliothek mit Musikabteilung", "Ausgeliehen - Fällig am: 24.9.2026"),
    ("Charlottenburg-Wilmersdorf: Dietrich-Bonhoeffer-Bibliothek", "Nicht im Regal"),
    ("ZLB: Amerika-Gedenkbibliothek (AGB)", "Nicht im Regal"),
    ("ZLB: Amerika-Gedenkbibliothek (AGB)", "Ausgeliehen - Fällig am: 10.9.2026"),
    ("ZLB: Amerika-Gedenkbibliothek (AGB)", "Ausgeliehen - Fällig am: 16.9.2026"),
    ("ZLB: Amerika-Gedenkbibliothek (AGB)", "Ausgeliehen - Fällig am: 14.9.2026"),
]


def test_extract_holdings_from_table_rows_astragal_multiple_copies_one_branch():
    holdings = extract_holdings_from_table_rows(ASTRAGAL_EXEMPLARE_ROWS, BRANCHES)
    # Only the 4 ZLB copies are a target branch (Charlottenburg-Wilmersdorf isn't one).
    assert len(holdings) == 4
    assert all(h.branch_id == "zlb" for h in holdings)
    # All 4 -- including the "Nicht im Regal" one -- now classify as on_loan,
    # not a mix of on_loan and unknown.
    assert all(h.status == "on_loan" for h in holdings)


def test_merge_holdings_combines_two_editions_deduping_identical_pairs():
    # Simulates search_book's real case (2026-09-13, "Educated"): the
    # ISBN13-specific edition shows no target-branch holdings at all, but
    # the title+author search turns up a different edition that does --
    # both real Exemplarangaben reads, just from two different catalog
    # records for the same title.
    from_isbn13 = []  # this edition wasn't at any target branch
    from_title_author = [
        BranchHolding(branch_id="zlb", branch_label="ZLB", matched_name="ZLB", status="available"),
        BranchHolding(branch_id="zlb", branch_label="ZLB", matched_name="ZLB", status="on_loan"),
    ]
    merged = _merge_holdings(from_isbn13, from_title_author)
    assert len(merged) == 2
    assert {h.status for h in merged} == {"available", "on_loan"}

    # A second, identical (branch, status) pair from the same or another
    # search shouldn't be duplicated.
    more = _merge_holdings(
        merged, [BranchHolding(branch_id="zlb", branch_label="ZLB", matched_name="ZLB", status="available")]
    )
    assert len(more) == 2
