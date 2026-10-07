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
)


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def get(self, url, timeout=None):  # noqa: ARG002
        return self._response


async def _allowed(robots: str, url: str, ua: str = ROBOTS_USER_AGENT) -> bool:
    return await _check_robots_txt(_FakeClient(_FakeResponse(200, robots)), url, ua)


@pytest.mark.asyncio
async def test_a_path_rule_does_not_disallow_the_whole_site():
    robots = "User-agent: *\nDisallow: /private/\nAllow: /\n"
    assert await _allowed(robots, "https://site.example/page") is True
    assert await _allowed(robots, "https://site.example/private/x") is False


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
        async def get(self, url, timeout=None):  # noqa: ARG002
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
