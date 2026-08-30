from vobb_read.voebb import (
    BranchConfig,
    classify_status,
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
