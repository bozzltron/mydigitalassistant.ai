# Fetching: browsing, robots.txt, and the bounded crawl

How the agent reads the web, and the policy behind it. Two rules govern everything
here: **identify honestly**, and **obey robots.txt accurately**.

## The two fetch paths

- **`fetch_url` (the tool)** — the agent's own browsing. `pipeline/tools.py`,
  `_fetch_single_url`. Strips HTML, checks robots.txt, caps the body at 500 KB, and
  optionally follows same-site links.
- **Search enrichment** — `orchestrator._fetch_url_body`, used to pull the top
  search-result pages in parallel for fact extraction. It goes through the **same**
  `_fetch_page` as the tool, so robots.txt and the per-hop SSRF check apply here
  too. It previously had its own fetch — `follow_redirects=True`, no robots check,
  a duplicated HTML stripper — which was a second policy *and* a weaker SSRF
  posture; that is why there is one `_fetch_page` and not two.

## robots.txt

Parsed with the stdlib `urllib.robotparser`, which understands per-User-agent and
per-path rules.

- **The token is `AssistantBot`.** `robotparser` takes the part of a UA before the
  first `/`; for `Mozilla/5.0 (compatible; AssistantBot/1.0)` that is `Mozilla`. So
  the product token is passed explicitly — otherwise a site that writes
  `User-agent: AssistantBot` would not bind us.
- **Absent or unreadable robots.txt = allow.** It is an opt-out; its absence is not
  a prohibition.
- **Regression:** the old check was a substring match — `"disallow: /"` is a
  substring of `"disallow: /private/"`, so any site with a single path-specific rule
  was treated as *fully* disallowed. That is why the agent was "often blocked by
  robots.txt" while nothing had actually disallowed it. See
  `tests/test_fetch_robots.py`.

### The AI-block caveat (letter vs intent)

An AI-crawler block (`User-agent: GPTBot`, `CCBot`, `Google-Extended`) names another
token, so it does **not** bind `AssistantBot` by the *letter* of robots.txt. But its
*intent* is "don't let AI consume my content", and an LLM assistant reading it is
that kind of client. The current behaviour honours the letter (we are allowed unless
`*` or `AssistantBot` disallows); the intent is a standing judgement, not a solved
problem. When a page is genuinely disallowed the honest move is to tell the user
rather than silently fetch — their browser is not bound by robots.txt.

## Honest user-agent

`Mozilla/5.0 (compatible; AssistantBot/1.0)`. The project does **not** spoof a
browser UA to evade a block: it identifies as a bot and respects what robots says
about bots. A browser-like UA would be deception, and deception is what this project
is built against.

## The bounded crawl (`follow_links`)

`fetch_url(url, follow_links=N)` (N = 0–5) reads further into the same site:

- **Same host only** — links off-site are dropped.
- **Deduped** — fragment dropped, query kept; the base URL excluded.
- **robots.txt checked per URL** — every followed link, not just the first.
- **Paced** — a short delay between fetches, so "go deeper" is not a crawl that
  hammers a site.
- **SSRF-checked every hop** — `safe_stream` re-runs the private/loopback check on
  each request and redirect. A crawl multiplies that surface; the per-hop check is
  not optional.
- **Skipped, not aborted** — a link that fails, is off-site, or is disallowed is
  dropped; the first page is still returned.

Each followed page appears under a `## <url>` header.

## Known limits

- **JS-rendered pages** return an error ("empty after HTML strip"); there is no
  headless browser, deliberately — it is a heavy dependency against a local,
  lightweight design. Fall back to a search snippet.
- **3,000 characters per page** reach the model (`_snippet`); longer pages are
  truncated with an explicit marker.
