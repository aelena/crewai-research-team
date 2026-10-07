from types import SimpleNamespace

from research_team.guardrails import (
    REPORT_SECTIONS,
    Guard,
    known_citations,
    ledger_integrity,
    min_citations,
    no_placeholders,
    sections,
)
from research_team.models import Claim, ClaimLedger
from research_team.sources import SourceRegistry, extract_urls, normalize


def test_extract_and_normalize():
    text = "See (https://www.Example.test/a/?utm_source=li&id=3#frag), and https://b.test/x."
    assert extract_urls(text) == ["https://www.Example.test/a/?utm_source=li&id=3#frag", "https://b.test/x"]
    assert normalize("http://www.Example.test/a/?utm_source=li&id=3#frag") == "https://example.test/a?id=3"


def test_registry_membership():
    reg = SourceRegistry()
    assert reg.observe("results: https://a.test/report?page=2 and https://b.test/", "search") == 2
    assert reg.observe("https://a.test/report?page=2", "scrape") == 0
    assert "https://www.a.test/report/?page=2" in reg
    assert "https://a.test/report" in reg  # query-insensitive fallback
    assert reg.unknown(["https://b.test", "https://invented.test/stat"]) == ["https://invented.test/stat"]
    [a, _] = reg.sources()
    assert a.origins == ["search", "scrape"]


def test_guard_retries_then_waves_through_and_reports():
    overrides = []
    g = Guard("t", [lambda raw, _: ["bad"] if "bad" in raw else []], retries=2, on_override=lambda n, p: overrides.append(n))
    assert g("bad")[0] is False
    assert g("bad")[0] is False
    ok, out = g("bad")  # third attempt = last allowed: passes, flagged
    assert ok and out == "bad" and overrides == ["t"]
    assert Guard("t", [lambda raw, _: []])("anything") == (True, "anything")


def test_strict_guard_never_waves_through():
    g = Guard("t", [lambda raw, _: ["bad"]], retries=0, strict=True)
    assert all(g("x")[0] is False for _ in range(5))


def test_guard_as_function_is_named():
    fn = Guard("report", []).as_function()
    assert fn.__name__ == "guard_report" and fn("x") == (True, "x")


def test_report_sections():
    check = sections(*REPORT_SECTIONS)
    assert len(check("# T\n\nbody", None)) == 3
    assert check("## Executive summary\n## Insights and recommendations\n## References", None) == []


def test_known_citations():
    reg = SourceRegistry()
    check = known_citations(reg)
    assert check("https://anything.test", None) == []  # empty registry: no tools ran, nothing to compare
    reg.observe("https://real.test/a", "search")
    assert check("[x](https://real.test/a)", None) == []
    [problem] = check("[x](https://real.test/a) [y](https://made-up.test/b)", None)
    assert "made-up.test" in problem and "real.test" not in problem


def test_placeholders_vs_real_links():
    assert no_placeholders("A claim [source](https://a.test).", None) == []
    assert no_placeholders("A claim [source].", None)
    assert no_placeholders("See https://example.com/x", None)


def test_min_citations():
    assert min_citations(2)("https://a.test https://b.test", None) == []
    assert min_citations(3)("https://a.test", None)


def test_ledger_integrity():
    def out(*claims):
        return SimpleNamespace(pydantic=ClaimLedger(track="main", claims=list(claims)))

    good = Claim(statement="s", status="verified", confidence="high", sources=["https://a.test"])
    unsourced = Claim(statement="t", status="verified", confidence="high")
    unverified = Claim(statement="u", status="unverified", confidence="low")
    assert ledger_integrity("", out(good, unverified)) == []
    assert len(ledger_integrity("", out(unsourced))) == 1
    assert "No claim is verified" in ledger_integrity("", out(unverified))[0]
