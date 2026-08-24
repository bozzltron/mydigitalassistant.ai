"""Phase 9A regression tests: association strengthening + canonical frame resolution.

Covers the two audited defects:
- F1: re-extracted associations froze at confidence 0.5 (IntegrityError swallow).
- F2: near-duplicate frame names fragmented the graph (exact-name match only).
"""

import pytest

from assistant.backend.memory.models import Association
from assistant.backend.pipeline.extractor import (
    EXTRACTION_PROMPT,
    SEARCH_EXTRACTION_PROMPT,
    ExtractedAssociation,
    ExtractedSlot,
    ExtractionResult,
    apply_extraction,
    normalize_frame_name,
    resolve_or_create_frame,
)

DIM = 768


async def _flat_embedding(text: str) -> list[float]:
    return [1.0] + [0.0] * (DIM - 1)


async def _seed_frame(store, name: str, ftype: str = "entity", **kwargs):
    return await store.create_frame(name, ftype, **kwargs)


# --- F1: association strength ---


async def test_create_association_bumps_on_duplicate(store):
    a = await _seed_frame(store, "album")
    b = await _seed_frame(store, "song")

    first = await store.create_association(
        from_frame_id=a.id, to_frame_id=b.id, relation_type="has_song"
    )
    second = await store.create_association(
        from_frame_id=a.id, to_frame_id=b.id, relation_type="has_song"
    )

    assert isinstance(second, Association)
    assert second.id == first.id
    assert first.confidence == pytest.approx(0.5)
    assert second.confidence > 0.5


async def test_apply_extraction_strengthening_across_sessions(store):
    """Same edge extracted twice -> one row, confidence strictly above 0.5."""
    extraction = ExtractionResult(
        slots=[
            ExtractedSlot(
                frame_name="mountain_and_the_wolf",
                frame_type="entity",
                key="artist",
                value="mozworth",
            ),
            ExtractedSlot(
                frame_name="mozworth", frame_type="entity", key="genre", value="indie rock"
            ),
        ],
        associations=[],
    )
    extraction.associations.append(
        ExtractedAssociation(
            from_frame="mountain_and_the_wolf",
            to_frame="mozworth",
            relation_type="created_by",
        )
    )

    first = await apply_extraction(extraction, store)
    second = await apply_extraction(extraction, store)

    assocs = await store.get_all_associations_for_frame(
        (await store.get_frame_by_name("mountain_and_the_wolf")).id
    )
    matching = [a for a in assocs if a.relation_type == "created_by"]
    assert len(matching) == 1
    assert first["associations_created"] >= 1
    # Second pass must NOT report a new row; strength grew instead.
    assert second["associations_created"] == 1
    assert matching[0].confidence > 0.5


# --- F2: canonical frame resolution ---


def test_normalize_frame_name_variants():
    assert normalize_frame_name("The Mountain & the Wolf") == "mountain wolf"
    assert normalize_frame_name("the mountain and the wolf") == "mountain wolf"
    assert normalize_frame_name("A Star Is Born!") == "star is born"
    assert normalize_frame_name("  Mozart ") == "mozart"
    assert normalize_frame_name("The The") == ""  # degenerate -> no fuzzy/exact-norm


async def test_normalized_name_reuses_existing_frame(store):
    existing = await _seed_frame(store, "The Mountain & The Wolf")

    resolved = await resolve_or_create_frame(store, "the mountain and the wolf", "entity")

    assert resolved == existing.id
    assert len(await store.list_frames()) == 1


async def test_fuzzy_resolution_within_distance_threshold(store):
    existing = await _seed_frame(store, "mountain_wolf_album")
    await store.embed_frames([existing.id], _flat_embedding)

    resolved = await resolve_or_create_frame(
        store, "mountain_wolf_lp", "entity", embed_fn=_flat_embedding
    )

    # Names normalize differently but embed identically -> fuzzy reuse.
    assert resolved == existing.id


async def test_fuzzy_resolution_respects_owner_isolation(store):
    user = await store.create_user("alice")
    private = await _seed_frame(store, "mountain_wolf_album", owner_user_id=user.id)
    await store.embed_frames([private.id], _flat_embedding)

    resolved = await resolve_or_create_frame(
        store, "mountain_wolf_lp", "entity", embed_fn=_flat_embedding
    )

    assert resolved != private.id
    created = await store.get_frame_by_name("mountain_wolf_lp")
    assert created is not None
    assert created.owner_user_id is None


async def test_fuzzy_skipped_for_short_names(store):
    bob = await _seed_frame(store, "bob", ftype="person")
    await store.embed_frames([bob.id], _flat_embedding)

    resolved = await resolve_or_create_frame(store, "bo", "person", embed_fn=_flat_embedding)

    assert resolved != bob.id


async def test_fuzzy_requires_compatible_types(store):
    album = await _seed_frame(store, "mountain_wolf_album", ftype="album")
    await store.embed_frames([album.id], _flat_embedding)

    resolved = await resolve_or_create_frame(
        store, "mountain_wolf_record", "event", embed_fn=_flat_embedding
    )

    # Neither side is the "entity" catch-all and types differ -> no merge.
    assert resolved != album.id


async def test_no_embedder_still_resolves_exact_and_normalized(store):
    existing = await _seed_frame(store, "The Mountain & The Wolf")
    resolved = await resolve_or_create_frame(store, "THE MOUNTAIN AND THE WOLF", "event")
    assert resolved == existing.id


# --- A3: extraction prompt guardrails ---


@pytest.mark.parametrize("prompt", [EXTRACTION_PROMPT, SEARCH_EXTRACTION_PROMPT])
def test_prompt_guardrails_invariants(prompt):
    flat = " ".join(prompt.split())
    assert "NEVER use a bare type word" in flat
    assert "at most 4 associations" in flat
