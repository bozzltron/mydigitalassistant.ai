"""Regression: session reads must be owner-scoped, bounded, and honestly labelled.

Three separate defects lived in the same pair of store methods:

1. `get_episodes_for_session` had no `user_id` filter and no SQL `LIMIT`. It
   returned every turn of any session whose id the caller supplied, and it grew
   without bound with the conversation. Every request-path caller then filtered
   by owner and/or sliced `[-N:]` in Python -- after paying for the whole read.
   Retrieval passed the unfiltered list straight into the prompt.
2. The `limit=0` case was a no-op: `episodes[-0:]` is the *whole* list, not an
   empty one, so a caller asking for zero turns silently got all of them.
3. `get_sessions_for_user` labelled each session with
   `MAX(CASE WHEN role = 'user' THEN content END)` -- and `MAX()` over TEXT is
   the *lexicographic* maximum. A session whose opening turn was "aaa first" and
   latest was "zzz last" was labelled "zzz last". The label is the conversation
   list's only description, so this was user-visible.

The signature keeps `user_id` and `limit` optional for the background callers
that genuinely want a whole session, so these tests pin both the scoped and the
unscoped behaviour.
"""

from __future__ import annotations

import pytest

from assistant.backend.memory.store import MemoryStore


class TestOwnerScoping:
    @pytest.mark.asyncio
    async def test_a_shared_session_id_does_not_leak_across_users(self, store: MemoryStore):
        """Two household members can pick the same session string by accident."""
        alice = await store.create_user("alice")
        bob = await store.create_user("bob")
        shared = "same-session-string"

        await store.create_episode(alice.id, shared, "user", "alice secret")
        await store.create_episode(bob.id, shared, "user", "bob secret")

        alice_turns = await store.get_episodes_for_session(shared, user_id=alice.id)
        bob_turns = await store.get_episodes_for_session(shared, user_id=bob.id)

        assert [e.content for e in alice_turns] == ["alice secret"]
        assert [e.content for e in bob_turns] == ["bob secret"]

    @pytest.mark.asyncio
    async def test_unscoped_read_still_returns_everyone_for_maintenance(
        self, store: MemoryStore
    ):
        """No user_id means "all owners" -- the export/summariser path relies on it."""
        alice = await store.create_user("alice")
        bob = await store.create_user("bob")
        await store.create_episode(alice.id, "s", "user", "a")
        await store.create_episode(bob.id, "s", "user", "b")

        all_turns = await store.get_episodes_for_session("s")

        assert {e.content for e in all_turns} == {"a", "b"}


class TestBounding:
    @pytest.mark.asyncio
    async def test_limit_returns_the_most_recent_turns_oldest_first(
        self, store: MemoryStore
    ):
        """The chat UI wants the tail of the conversation, in order."""
        user = await store.create_user("alice")
        for i in range(5):
            await store.create_episode(user.id, "s", "user", f"turn {i}")

        tail = await store.get_episodes_for_session("s", user_id=user.id, limit=2)

        assert [e.content for e in tail] == ["turn 3", "turn 4"]

    @pytest.mark.asyncio
    async def test_a_zero_limit_reads_the_whole_session_not_zero_turns(
        self, store: MemoryStore
    ):
        """`episodes[-0:]` is the entire list; pin the explicit contract."""
        user = await store.create_user("alice")
        for i in range(3):
            await store.create_episode(user.id, "s", "user", f"turn {i}")

        whole = await store.get_episodes_for_session("s", user_id=user.id, limit=0)

        assert len(whole) == 3

    @pytest.mark.asyncio
    async def test_limit_applies_before_the_owner_filter_is_uselessly_wide(
        self, store: MemoryStore
    ):
        """The bound and the scope compose: the tail is the owner's tail."""
        alice = await store.create_user("alice")
        bob = await store.create_user("bob")
        await store.create_episode(alice.id, "s", "user", "a1")
        await store.create_episode(bob.id, "s", "user", "b1")
        await store.create_episode(alice.id, "s", "user", "a2")
        await store.create_episode(bob.id, "s", "user", "b2")

        tail = await store.get_episodes_for_session("s", user_id=alice.id, limit=1)

        assert [e.content for e in tail] == ["a2"]


class TestSessionLabel:
    @pytest.mark.asyncio
    async def test_label_is_the_first_user_message_not_the_lexicographic_max(
        self, store: MemoryStore
    ):
        """The bug: "zzz last" won over "aaa first" purely on spelling."""
        user = await store.create_user("alice")
        await store.create_session("s", user.id)
        await store.create_episode(user.id, "s", "user", "aaa first")
        await store.create_episode(user.id, "s", "assistant", "reply")
        await store.create_episode(user.id, "s", "user", "zzz last")

        sessions = await store.get_sessions_for_user(user.id)

        assert sessions[0]["last_message"] == "aaa first"

    @pytest.mark.asyncio
    async def test_label_skips_leading_assistant_and_empty_turns(self, store: MemoryStore):
        user = await store.create_user("alice")
        await store.create_session("s", user.id)
        await store.create_episode(user.id, "s", "assistant", "greeting")
        await store.create_episode(user.id, "s", "user", "")
        await store.create_episode(user.id, "s", "user", "the real opening")

        sessions = await store.get_sessions_for_user(user.id)

        assert sessions[0]["last_message"] == "the real opening"

    @pytest.mark.asyncio
    async def test_explicit_title_still_wins(self, store: MemoryStore):
        """The first-message fallback must not override a real title."""
        user = await store.create_user("alice")
        await store.create_session("s", user.id, title="Garden planning")
        await store.create_episode(user.id, "s", "user", "zzz")

        sessions = await store.get_sessions_for_user(user.id)

        assert sessions[0]["last_message"] == "Garden planning"
