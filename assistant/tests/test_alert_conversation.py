"""Regression: an alert is the first message of a conversation, and answering it closes it.

Phase C.2. The normal flow is user-initiated — the user speaks, the agent answers. An
alert inverts it: **the agent opens with the problem and the user replies.** The alert
carries its own resolution instructions, which is the agent's message to itself, and
it is how the agent knows what "resolved" means for this alert when the user's reply
arrives.

Resolution closes the alert **two ways**, deliberately:

1. the agent's own instruction inside the alert prompt, and
2. a deterministic backstop: when a turn lands in the session an alert opened, the
   alert closes.

The second exists because relying only on the model remembering to close its own
alert is the failure this project has already been bitten by. The model decides *what*
to settle; the plumbing guarantees the close.

Worth stating because it is the whole design rationale: `is_read` never worked. 103
rows sat unread because reading an alert accomplished nothing. An alert that closes by
being *answered* has a reason to be answered.
"""

from __future__ import annotations

import pytest

from assistant.backend.memory.store import ALERT_FRAME_TYPE


async def _an_alert(store, user_id: int, **kw):
    defaults = {
        "type": "conflict",
        "title": "Two beliefs about your city",
        "message": "I hold both 'Austin' and 'Austin, TX' and cannot tell which is right.",
        "severity": "important",
        "about": "austin_tx",
    }
    defaults.update(kw)
    return await store.create_alert(user_id=user_id, **defaults)


class TestAlertStartsTheConversation:
    """The inversion: the agent speaks first."""

    @pytest.mark.asyncio
    async def test_first_message_is_from_the_assistant(self, store):
        user = await store.create_user("alice")
        alert = await _an_alert(store, user.id)

        session_id, episode_id = await store.open_alert_conversation(alert.id, user.id)
        episodes = await store.get_episodes_for_session(session_id, user_id=user.id)

        assert episodes, "the conversation has no turns"
        assert episodes[0].role == "assistant", (
            "the alert did not open the conversation — the user's turn is first"
        )
        assert episodes[0].id == episode_id

    @pytest.mark.asyncio
    async def test_opening_message_carries_the_alert(self, store):
        user = await store.create_user("alice")
        alert = await _an_alert(store, user.id)
        session_id, _ = await store.open_alert_conversation(alert.id, user.id)

        episodes = await store.get_episodes_for_session(session_id, user_id=user.id)
        opening = episodes[0].content
        assert "Austin" in opening
        assert "Two beliefs about your city" in opening

    @pytest.mark.asyncio
    async def test_opening_message_carries_its_own_resolution_instructions(self, store):
        """The agent's message to itself: how does this alert close?"""
        user = await store.create_user("alice")
        alert = await _an_alert(store, user.id)
        session_id, _ = await store.open_alert_conversation(alert.id, user.id)

        episodes = await store.get_episodes_for_session(session_id, user_id=user.id)
        opening = episodes[0].content.lower()
        assert "mark this alert resolved" in opening
        # And it names what the resolution is about, so the target is unambiguous.
        assert "austin_tx" in opening

    @pytest.mark.asyncio
    async def test_alert_links_to_its_conversation(self, store):
        user = await store.create_user("alice")
        alert = await _an_alert(store, user.id)
        session_id, _ = await store.open_alert_conversation(alert.id, user.id)

        slots = {s.key: s.value for s in await store.get_slots_for_frame(alert.id)}
        assert slots["session_id"] == session_id

    @pytest.mark.asyncio
    async def test_reopening_does_not_fork_the_thread(self, store):
        """Two conversations for one alert would be two places to resolve it."""
        user = await store.create_user("alice")
        alert = await _an_alert(store, user.id)

        first_session, first_episode = await store.open_alert_conversation(
            alert.id, user.id
        )
        second_session, second_episode = await store.open_alert_conversation(
            alert.id, user.id
        )

        assert first_session == second_session
        assert first_episode == second_episode
        episodes = await store.get_episodes_for_session(first_session, user_id=user.id)
        assert len(episodes) == 1, "reopening wrote a second opening message"

    @pytest.mark.asyncio
    async def test_a_non_alert_frame_is_refused(self, store):
        user = await store.create_user("alice")
        frame = await store.create_frame("just_a_fact", "entity", owner_user_id=user.id)
        with pytest.raises(ValueError):
            await store.open_alert_conversation(frame.id, user.id)


class TestResolutionClosesIt:
    """Belt and braces: the model decides, the plumbing guarantees."""

    @pytest.mark.asyncio
    async def test_reply_in_the_session_resolves_the_alert(self, store):
        """The deterministic backstop. A user reply landing in the alert's
        conversation means they have engaged with it, so it closes."""
        user = await store.create_user("alice")
        alert = await _an_alert(store, user.id)
        session_id, _ = await store.open_alert_conversation(alert.id, user.id)
        assert await store.get_unread_alert_count(user.id) == 1

        resolved = await store.resolve_alerts_for_session(session_id)
        assert resolved == 1
        assert await store.get_unread_alert_count(user.id) == 0

    @pytest.mark.asyncio
    async def test_an_unrelated_session_resolves_nothing(self, store):
        user = await store.create_user("alice")
        alert = await _an_alert(store, user.id)
        await store.open_alert_conversation(alert.id, user.id)

        resolved = await store.resolve_alerts_for_session("conv_somewhere_else")
        assert resolved == 0
        assert await store.get_unread_alert_count(user.id) == 1

    @pytest.mark.asyncio
    async def test_the_agent_instruction_also_works(self, store):
        """The second half: the agent marking its own alert resolved, which is what
        the opening message tells it to do."""
        user = await store.create_user("alice")
        alert = await _an_alert(store, user.id)
        await store.open_alert_conversation(alert.id, user.id)

        assert await store.resolve_alert(alert.id) is True
        assert await store.get_unread_alert_count(user.id) == 0

    @pytest.mark.asyncio
    async def test_resolving_twice_is_harmless(self, store):
        user = await store.create_user("alice")
        alert = await _an_alert(store, user.id)
        await store.open_alert_conversation(alert.id, user.id)

        assert await store.resolve_alert(alert.id) is True
        assert await store.resolve_alert(alert.id) is True
        assert await store.get_unread_alert_count(user.id) == 0

    @pytest.mark.asyncio
    async def test_resolution_is_recorded_in_slot_history(self, store):
        """Resolution is a belief change like any other, so it is auditable."""
        user = await store.create_user("alice")
        alert = await _an_alert(store, user.id)
        await store.resolve_alert(alert.id)

        slots = await store.get_slots_for_frame(alert.id)
        status_slot = next(s for s in slots if s.key == "status")
        assert status_slot.value == "resolved"


class TestAFullAlertLifecycle:
    @pytest.mark.asyncio
    async def test_open_reply_resolve(self, store):
        """The whole loop, as the user experiences it."""
        user = await store.create_user("alice")
        alert = await _an_alert(store, user.id)
        assert alert.id is not None

        # The bell lists it.
        open_alerts = await store.get_alerts(user.id)
        assert [a.id for a in open_alerts] == [alert.id]

        # Opening it starts an agent-initiated conversation.
        session_id, _ = await store.open_alert_conversation(alert.id, user.id)
        episodes = await store.get_episodes_for_session(session_id, user_id=user.id)
        assert episodes[0].role == "assistant"

        # The user replies.
        await store.create_episode(
            user_id=user.id,
            session_id=session_id,
            role="user",
            content="Austin, TX — the fuller one.",
        )
        # Which closes it.
        await store.resolve_alerts_for_session(session_id)
        assert await store.get_alerts(user.id) == []
        assert await store.get_unread_alert_count(user.id) == 0

    @pytest.mark.asyncio
    async def test_alert_frame_survives_resolution(self, store):
        """Resolved, not deleted: the record of what was asked stays."""
        user = await store.create_user("alice")
        alert = await _an_alert(store, user.id)
        await store.resolve_alert(alert.id)

        frame = await store.get_frame(alert.id)
        assert frame is not None
        assert frame.type == ALERT_FRAME_TYPE
        # And the audit trail is intact.
        slots = {s.key: s.value for s in await store.get_slots_for_frame(alert.id)}
        assert slots["title"] == "Two beliefs about your city"
        assert slots["status"] == "resolved"
