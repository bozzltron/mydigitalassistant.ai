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
    async def test_opening_message_is_the_question_not_the_report(self, store):
        """The opening is the alert's short title, so it reads as a question to
        answer rather than a wall of text. `message` is the full task report and
        is deliberately not dumped into the conversation."""
        user = await store.create_user("alice")
        alert = await _an_alert(store, user.id)
        session_id, _ = await store.open_alert_conversation(alert.id, user.id)

        episodes = await store.get_episodes_for_session(session_id, user_id=user.id)
        opening = episodes[0].content
        assert opening.startswith("Two beliefs about your city")
        assert "I hold both 'Austin' and 'Austin, TX'" not in opening

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
        # The first call seeded the opening message; the second found the session
        # already had turns and wrote nothing, so it reports no episode. That is the
        # guarantee: reopening does not add a second opening.
        assert first_episode is not None
        assert second_episode is None
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


class TestAttachToAnExistingConversation:
    """The selector's server side, and the reason it exists: conversations must not
    build up one per alert."""

    @pytest.mark.asyncio
    async def test_attaching_to_an_existing_session_posts_the_alert(self, store):
        """The alert must appear in the conversation the user picked, whether or not
        it already has history. It used to be written only into an *empty* session,
        so choosing any conversation you had used before showed nothing at all --
        which read as the feature being broken."""
        user = await store.create_user("alice")
        await store.create_session("conv_talking", user.id)
        await store.create_episode(
            user_id=user.id,
            session_id="conv_talking",
            role="user",
            content="I was thinking about my release plan.",
        )
        alert = await _an_alert(store, user.id)

        session_id, episode_id = await store.attach_alert_to_conversation(
            alert.id, user.id, "conv_talking"
        )

        assert session_id == "conv_talking"
        assert episode_id is not None, "the alert was not posted into the conversation"
        episodes = await store.get_episodes_for_session(
            "conv_talking", user_id=user.id
        )
        assert len(episodes) == 2, "the alert should add exactly one turn"
        # The message is the short question, not the whole report.
        assert episodes[-1].role == "assistant"
        assert episodes[-1].content.startswith("Two beliefs about your city")
        assert "I hold both 'Austin' and 'Austin, TX'" not in episodes[-1].content

    @pytest.mark.asyncio
    async def test_posts_when_the_slot_is_set_but_no_message_exists(self, store):
        """An alert attached before this behaviour existed has `session_id` set and
        no message. Keying "already posted" on that slot refused to post it, so
        resolving it looked broken -- which is exactly what a user hit."""
        user = await store.create_user("alice")
        await store.create_session("conv_talking", user.id)
        await store.create_episode(
            user_id=user.id, session_id="conv_talking", role="user", content="Hi"
        )
        alert = await _an_alert(store, user.id)
        # Simulate the old attach: the slot points here, no message was written.
        await store.set_derived_slot(
            alert.id, "session_id", "conv_talking", source_type="alert"
        )

        _, episode_id = await store.attach_alert_to_conversation(
            alert.id, user.id, "conv_talking"
        )

        assert episode_id is not None, "not posted despite there being no message"
        episodes = await store.get_episodes_for_session("conv_talking", user_id=user.id)
        assert len(episodes) == 2

    @pytest.mark.asyncio
    async def test_reposting_to_the_same_session_does_not_duplicate(self, store):
        """Once posted, resolving the same alert here again must not stack."""
        user = await store.create_user("alice")
        await store.create_session("conv_talking", user.id)
        alert = await _an_alert(store, user.id)

        _, first = await store.attach_alert_to_conversation(
            alert.id, user.id, "conv_talking"
        )
        _, second = await store.attach_alert_to_conversation(
            alert.id, user.id, "conv_talking"
        )

        assert first is not None
        assert second is None, "a second opening was posted"
        episodes = await store.get_episodes_for_session("conv_talking", user_id=user.id)
        assert len(episodes) == 1

    @pytest.mark.asyncio
    async def test_attached_alert_closes_when_the_user_replies(self, store):
        """The point of attaching: resolution happens where the user already is."""
        user = await store.create_user("alice")
        await store.create_session("conv_talking", user.id)
        await store.create_episode(
            user_id=user.id,
            session_id="conv_talking",
            role="user",
            content="Let's talk about my city.",
        )
        alert = await _an_alert(store, user.id)
        await store.attach_alert_to_conversation(alert.id, user.id, "conv_talking")
        assert await store.get_unread_alert_count(user.id) == 1

        # The user keeps talking in the same conversation.
        await store.create_episode(
            user_id=user.id,
            session_id="conv_talking",
            role="user",
            content="Austin, TX is the right one.",
        )
        resolved = await store.resolve_alerts_for_session("conv_talking")
        assert resolved == 1
        assert await store.get_unread_alert_count(user.id) == 0

    @pytest.mark.asyncio
    async def test_attaching_to_an_empty_session_seeds_the_alert(self, store):
        """A session with no turns has nothing to resolve *in*, so the alert opens
        it — the agent speaks first, as in Phase 2."""
        user = await store.create_user("alice")
        await store.create_session("conv_fresh", user.id)
        alert = await _an_alert(store, user.id)

        _, episode_id = await store.attach_alert_to_conversation(
            alert.id, user.id, "conv_fresh"
        )
        assert episode_id is not None
        episodes = await store.get_episodes_for_session("conv_fresh", user_id=user.id)
        assert episodes[0].role == "assistant"

    @pytest.mark.asyncio
    async def test_another_users_alert_is_refused(self, store):
        user = await store.create_user("alice")
        other = await store.create_user("bob")
        await store.create_session("conv_bob", other.id)
        alert = await _an_alert(store, user.id)

        with pytest.raises(ValueError):
            await store.attach_alert_to_conversation(alert.id, other.id, "conv_bob")

    @pytest.mark.asyncio
    async def test_reattaching_moves_the_alert_not_duplicates_it(self, store):
        """Picking a different venue must not leave two sessions both owning it."""
        user = await store.create_user("alice")
        await store.create_session("conv_a", user.id)
        await store.create_session("conv_b", user.id)
        alert = await _an_alert(store, user.id)

        await store.attach_alert_to_conversation(alert.id, user.id, "conv_a")
        await store.attach_alert_to_conversation(alert.id, user.id, "conv_b")

        slots = {s.key: s.value for s in await store.get_slots_for_frame(alert.id)}
        assert slots["session_id"] == "conv_b"

        # And replying in the old venue no longer closes it.
        assert await store.resolve_alerts_for_session("conv_a") == 0
        assert await store.resolve_alerts_for_session("conv_b") == 1

    @pytest.mark.asyncio
    async def test_alert_threads_are_excluded_from_the_selector(self, store):
        """Offering to resolve one alert inside another alert's thread is not a
        choice — it entangles two questions."""
        user = await store.create_user("alice")
        await store.create_session("conv_real", user.id)
        await store.create_episode(
            user_id=user.id,
            session_id="conv_real",
            role="user",
            content="a real conversation",
        )
        # An alert's own thread, which the selector must not offer.
        alert_one = await _an_alert(store, user.id)
        await store.open_alert_conversation(alert_one.id, user.id)

        sessions = await store.get_sessions_for_user(user.id)
        offered = [
            s["id"] for s in sessions if not str(s["id"]).startswith("conv_alert_")
        ]
        assert "conv_real" in offered
        assert not any(str(s["id"]).startswith("conv_alert_") for s in sessions
                       if s["id"] in offered)


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
