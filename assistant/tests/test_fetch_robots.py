"""robots.txt is parsed per-User-agent and per-path, not by substring.

Regression: the old check tested `"disallow: /" in robots_text`, and that string
is a substring of `"disallow: /private/"`. So a single path-specific rule marked
the *whole* site disallowed -- which is nearly every site, and the reason the agent
was "often blocked by robots.txt" while nothing had actually disallowed it.
"""

from __future__ import annotations

import pytest

from assistant.backend.pipeline.tools import (
    FETCH_USER_AGENT,
    ROBOTS_USER_AGENT,
    _check_robots_txt,
    clear_robots_cache,
)


@pytest.fixture(autouse=True)
def _empty_robots_cache():
    """Each test starts with no cached documents, and leaves none behind."""
    clear_robots_cache()
    yield
    clear_robots_cache()


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def get(self, url, timeout=None):
        return self._response


class _CountingClient(_FakeClient):
    """A fake client that records what it was asked for."""

    def __init__(self, response: _FakeResponse) -> None:
        super().__init__(response)
        self.calls: list[str] = []

    async def get(self, url, timeout=None):
        self.calls.append(url)
        return await super().get(url, timeout)


async def _allowed(robots: str, url: str, ua: str = ROBOTS_USER_AGENT) -> bool:
    return await _check_robots_txt(_FakeClient(_FakeResponse(200, robots)), url, ua)


@pytest.mark.asyncio
async def test_a_path_rule_does_not_disallow_the_whole_site():
    robots = "User-agent: *\nDisallow: /private/\nAllow: /\n"
    assert await _allowed(robots, "https://site.example/page") is True
    assert await _allowed(robots, "https://site.example/private/x") is False


@pytest.mark.asyncio
async def test_robots_is_fetched_once_per_host():
    """One document per site, not one per URL.

    A `follow_links` crawl or a task reading several articles from one host paid a
    5s robots.txt request per page, on the turn's critical path.
    """
    client = _CountingClient(_FakeResponse(200, "User-agent: *\nDisallow: /private/\n"))
    assert await _check_robots_txt(client, "https://site.example/a", ROBOTS_USER_AGENT)
    assert await _check_robots_txt(client, "https://site.example/b", ROBOTS_USER_AGENT)
    assert len(client.calls) == 1, client.calls


@pytest.mark.asyncio
async def test_the_cached_document_still_applies_per_path():
    """The cache holds the document, not a verdict — a disallowed path does not
    block the host."""
    client = _CountingClient(_FakeResponse(200, "User-agent: *\nDisallow: /private/\n"))
    assert not await _check_robots_txt(
        client, "https://site.example/private/x", ROBOTS_USER_AGENT
    )
    assert await _check_robots_txt(client, "https://site.example/article", ROBOTS_USER_AGENT)
    assert len(client.calls) == 1, client.calls


@pytest.mark.asyncio
async def test_the_cache_expires(monkeypatch):
    """Past the TTL the document is re-read, so a changed robots.txt is noticed."""
    from assistant.backend.pipeline import tools as tools_module

    clock = {"now": 1000.0}
    monkeypatch.setattr(tools_module.time, "monotonic", lambda: clock["now"])

    client = _CountingClient(_FakeResponse(200, "User-agent: *\nDisallow: /\n"))
    await _check_robots_txt(client, "https://site.example/a", ROBOTS_USER_AGENT)
    assert len(client.calls) == 1

    clock["now"] += tools_module.ROBOTS_CACHE_TTL_SECONDS + 1
    await _check_robots_txt(client, "https://site.example/b", ROBOTS_USER_AGENT)
    assert len(client.calls) == 2, client.calls


@pytest.mark.asyncio
async def test_a_refusal_is_logged_with_the_url_and_the_token(caplog):
    """A blocked fetch must leave a trace.

    The tool result says "blocked by robots.txt" to the model, and the log said
    nothing — so an agent that fetched less in a task than in chat left no evidence
    of the reason.
    """
    import logging

    caplog.set_level(logging.INFO, logger="assistant.backend.pipeline.tools")
    allowed = await _allowed(
        "User-agent: *\nDisallow: /\n", "https://blocked.example/page"
    )
    assert allowed is False
    assert "robots.txt disallows https://blocked.example/page" in caplog.text
    assert ROBOTS_USER_AGENT in caplog.text


@pytest.mark.asyncio
async def test_a_blanket_disallow_blocks():
    assert (
        await _allowed("User-agent: *\nDisallow: /\n", "https://site.example/")
        is False
    )


@pytest.mark.asyncio
async def test_absent_robots_allows():
    client = _FakeClient(_FakeResponse(404))
    assert await _check_robots_txt(
        client, "https://site.example/x", ROBOTS_USER_AGENT
    ) is True


@pytest.mark.asyncio
async def test_unreadable_robots_allows():
    class _Boom:
        async def get(self, url, timeout=None):
            raise RuntimeError("network down")

    assert await _check_robots_txt(
        _Boom(), "https://site.example/x", ROBOTS_USER_AGENT
    ) is True


@pytest.mark.asyncio
async def test_a_rule_naming_our_token_binds():
    """A site that blocks `AssistantBot` by name is honoured. This is why the
    product token is passed to the parser: `robotparser` would read our UA's token
    as "Mozilla" and miss it."""
    robots = "User-agent: AssistantBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    assert await _allowed(robots, "https://site.example/page") is False


@pytest.mark.asyncio
async def test_an_ai_block_for_another_crawler_does_not_bind_by_letter():
    """`GPTBot` is not us, so the letter permits -- the owner's intent about AI
    clients is a separate, documented judgement (see assistant/AGENTS.md)."""
    robots = "User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    assert await _allowed(robots, "https://site.example/page") is True


def test_the_fetch_ua_identifies_us_honestly():
    assert "AssistantBot" in FETCH_USER_AGENT
    assert ROBOTS_USER_AGENT == "AssistantBot"
