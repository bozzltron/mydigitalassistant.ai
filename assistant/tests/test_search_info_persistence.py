"""Search/media payload persistence on episodes (Phase 3 R8).

Message imagery comes only from a turn's search results, so the payload has to
be stored with the assistant episode or the media is lost on reload and only the
text survives.
"""

import json

import pytest

from assistant.backend.db.schema import init_db
from assistant.backend.db.sqlcipher import aiosqlite_connect
from assistant.backend.memory.store import MemoryStore
from assistant.backend.pipeline.search import (
    SearchInfo,
    SearchResult,
    YouTubeVideo,
    search_info_payload,
)


@pytest.fixture
async def store(tmp_path):
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    return MemoryStore(db_path)


def _sample_info() -> SearchInfo:
    return SearchInfo(
        backend="brave",
        query="machu picchu video",
        results=[
            SearchResult(
                title="A",
                url="https://page/a",
                snippet="a long snippet that should not be stored",
                engine="brave",
                thumbnail="https://img/a.png",
                image="https://img/a-o.png",
            )
        ],
        video_results=[
            YouTubeVideo(
                video_id="abcdefghijk",
                title="V",
                channel_title="Chan",
                thumbnail_url="https://img/v.png",
                url="https://www.youtube.com/watch?v=abcdefghijk",
            )
        ],
    )


def test_payload_drops_snippets_and_engine():
    payload = json.loads(search_info_payload(_sample_info()))

    assert payload["backend"] == "brave"
    assert payload["query"] == "machu picchu video"
    assert payload["results"] == [
        {
            "title": "A",
            "url": "https://page/a",
            "thumbnail": "https://img/a.png",
            "image": "https://img/a-o.png",
        }
    ]
    assert payload["video_results"][0]["video_id"] == "abcdefghijk"
    # The bulk of a search — page snippets — is gone.
    assert "snippet" not in json.dumps(payload)


def test_payload_is_none_without_search():
    assert search_info_payload(None) is None


@pytest.mark.asyncio
async def test_episode_round_trips_search_info(store):
    user = await store.create_user("u")
    payload = search_info_payload(_sample_info())

    await store.create_episode(user.id, "s1", "assistant", "answer", search_info=payload)

    episodes = await store.get_episodes_for_session("s1", user_id=user.id)
    assert episodes[0].search_info == payload
    assert json.loads(episodes[0].search_info)["query"] == "machu picchu video"


@pytest.mark.asyncio
async def test_episode_without_search_has_no_payload(store):
    user = await store.create_user("u")
    await store.create_episode(user.id, "s2", "assistant", "answer")

    episodes = await store.get_episodes_for_session("s2", user_id=user.id)
    assert episodes[0].search_info is None


@pytest.mark.asyncio
async def test_migration_adds_search_info_column(tmp_path):
    db_path = str(tmp_path / "m.db")
    await init_db(db_path)

    async with aiosqlite_connect(db_path) as db:
        cols = {r[1] for r in await db.execute_fetchall("PRAGMA table_info(episodes)")}
    assert "search_info" in cols
