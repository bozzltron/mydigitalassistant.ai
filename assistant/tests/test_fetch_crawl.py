"""Bounded link-following for `fetch_url`: same-site, deduped, fragment-dropped.

The crawl itself is exercised through `_same_site_links` (the pure part) and the
robots gate (`test_fetch_robots.py`). The fetches are network calls; keeping the
link selection pure keeps the test hermetic.
"""

from __future__ import annotations

from assistant.backend.pipeline.tools import _same_site_links, _snippet


def test_same_site_links_resolves_and_filters():
    html = (
        '<a href="/about">About</a>'
        '<a href="https://other.example/x">Off-site</a>'
        '<a href="mailto:a@b.com">Mail</a>'
        '<a href="https://site.example/page#frag">Fragment</a>'
        '<a href="https://site.example/page">Dup</a>'
        '<a href="https://site.example/">Base</a>'
    )
    links = _same_site_links("https://site.example/", html, 10)
    assert links == ["https://site.example/about", "https://site.example/page"]


def test_same_site_links_caps_candidates():
    html = "".join(f'<a href="/p{i}">x</a>' for i in range(10))
    assert len(_same_site_links("https://site.example/", html, 3)) == 3


def test_same_site_links_survives_broken_html():
    # Best-effort: malformed markup yields no links, and must not raise.
    assert _same_site_links("https://site.example/", "<a href=", 5) == []


def test_snippet_marks_truncation():
    assert _snippet("short") == "short"
    out = _snippet("x" * 4000)
    assert out.startswith("x" * 3000)
    assert "truncated to first 3000" in out
