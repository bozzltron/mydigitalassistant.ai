"""The map file: validation, canonicalisation, and the GET/PUT round trip.

A map is a GeoJSON file in the sandbox (`docs/MAPS.md`). The failure guarded
hardest is a coordinate transposition: GeoJSON is `[lon, lat]`, and a swap is a
*valid* point in the ocean that renders confidently forever.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from assistant.backend.config import settings
from assistant.backend.main import _state, app, get_orchestrator, get_store
from assistant.backend.maps.geojson import (
    MAX_DESCRIPTION,
    GeoJSONError,
    dumps,
    empty_fc,
    validate_fc,
)
from assistant.backend.memory.retrieval import Retriever
from assistant.backend.pipeline.orchestrator import Orchestrator, OrchestratorDeps
from assistant.tests.conftest import SignedInClient


def _point(lon, lat, description=None):
    props = {"description": description} if description is not None else {}
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": props,
    }


def _fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


class TestValidate:
    def test_rounds_to_6dp_and_keeps_the_description(self):
        fc = validate_fc(_fc(_point(-122.6801234567, 45.523, "shed")))
        assert fc["features"][0]["geometry"]["coordinates"] == [-122.680123, 45.523]
        assert fc["features"][0]["properties"] == {"description": "shed"}

    def test_42_and_42_point_zero_are_one_point(self):
        assert validate_fc(_fc(_point(42, 42.0))) == validate_fc(_fc(_point(42.0, 42)))

    def test_out_of_range_is_refused(self):
        with pytest.raises(GeoJSONError):
            validate_fc(_fc(_point(200, 10)))
        with pytest.raises(GeoJSONError):
            validate_fc(_fc(_point(10, 91)))

    def test_a_half_coordinate_is_refused(self):
        with pytest.raises(GeoJSONError):
            validate_fc(
                _fc(
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [10]},
                        "properties": {},
                    }
                )
            )

    def test_zero_zero_is_refused(self):
        with pytest.raises(GeoJSONError):
            validate_fc(_fc(_point(0, 0)))

    def test_a_non_point_geometry_is_refused(self):
        with pytest.raises(GeoJSONError):
            validate_fc(
                _fc(
                    {
                        "type": "Feature",
                        "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
                        "properties": {},
                    }
                )
            )

    def test_a_non_feature_collection_is_refused(self):
        with pytest.raises(GeoJSONError):
            validate_fc({"type": "Feature", "geometry": None})

    def test_the_description_is_trimmed_and_capped(self):
        trimmed = validate_fc(_fc(_point(1, 1, "  hi  ")))
        assert trimmed["features"][0]["properties"]["description"] == "hi"
        capped = validate_fc(_fc(_point(1, 1, "x" * 5000)))
        assert len(capped["features"][0]["properties"]["description"]) == MAX_DESCRIPTION

    def test_a_missing_description_writes_no_null(self):
        fc = validate_fc(_fc(_point(1, 1)))
        assert fc["features"][0]["properties"] == {}


class TestDumps:
    def test_indented_and_trailing_newline(self):
        text = dumps(empty_fc())
        assert text.endswith("\n")
        assert "\n  " in text


@pytest.fixture
async def client(store, stub_llm, stub_search):
    """FastAPI TestClient with test dependencies wired in (mirrors test_api.py)."""
    retriever = Retriever(store=store, llm_client=stub_llm)
    orchestrator = Orchestrator(
        deps=OrchestratorDeps(
            store=store,
            retriever=retriever,
            llm_client=stub_llm,
            search_tool=stub_search,
        )
    )
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    _state["store"] = store
    _state["llm_client"] = stub_llm
    _state["retriever"] = retriever
    _state["orchestrator"] = orchestrator
    _state["search_tool"] = stub_search

    # The account the page acts as. `PUT /map` requires one: it used to default to
    # id 1, which assumed an account with that id existed.
    user = await store.create_user("mapper")

    original_db_path = settings.database_path
    original_scheduler = settings.scheduler_enabled
    settings.database_path = store.db_path
    settings.scheduler_enabled = False
    try:
        with TestClient(app) as c:
            _state["llm_client"] = stub_llm
            _state["search_tool"] = stub_search
            yield SignedInClient(c, user.id)
    finally:
        settings.database_path = original_db_path
        settings.scheduler_enabled = original_scheduler
        app.dependency_overrides.clear()
        _state.clear()


MAP_PATH = Path("/app/data/map.geojson")


@pytest.fixture(autouse=True)
def _clean_map_file():
    MAP_PATH.unlink(missing_ok=True)
    yield
    MAP_PATH.unlink(missing_ok=True)


class TestMapApi:
    async def test_absent_map_returns_an_empty_feature_collection(self, client):
        r = client.get("/map")
        assert r.status_code == 200
        assert r.json() == {"type": "FeatureCollection", "features": []}

    async def test_put_then_get_round_trips(self, client):
        payload = _fc(_point(-122.68, 45.523, "gate code 4412"))
        assert client.put("/map", json=payload).status_code == 200
        got = client.get("/map").json()
        assert got["features"][0]["geometry"]["coordinates"] == [-122.68, 45.523]
        assert got["features"][0]["properties"]["description"] == "gate code 4412"

    async def test_put_refuses_out_of_range_and_writes_nothing(self, client):
        assert client.put("/map", json=_fc(_point(200, 10))).status_code == 422
        assert not MAP_PATH.exists(), "a refused map must not be written"

    async def test_put_makes_the_map_visible_in_the_files_list(self, client):
        """A saved map is the user's file and must be findable as one.

        Regression: `PUT /map` wrote the bytes but skipped `apply_file_to_memory`
        — the step the upload endpoint, `write_file` and `edit_file` all funnel
        through. `GET /files/list` is a view over file *frames*, so map.geojson was
        on disk and readable by the agent but absent from the Files view: the
        user's own content, invisible to the user.
        """
        client.put("/map", json=_fc(_point(-122.68, 45.523, "shed")))

        listed = client.get("/files/list").json()
        entry = next((e for e in listed if e["file_name"] == "map.geojson"), None)
        assert entry is not None, [e["file_name"] for e in listed]
        assert entry["source_type"] == "file_create"
        assert entry["file_ext"] == "geojson"
        assert entry["file_size"] > 0

    async def test_repeated_saves_refresh_one_frame(self, client):
        """Saving again updates the map's frame rather than adding a second.

        The page PUTs the whole file on every change, so this runs on each edit.
        """
        for description in ("shed", "shed, gate code 4412"):
            client.put("/map", json=_fc(_point(-122.68, 45.523, description)))

        names = [e["file_name"] for e in client.get("/files/list").json()]
        assert names.count("map.geojson") == 1

    async def test_the_file_is_written_even_when_memory_cannot_be(self, client):
        """A memory failure must not fail a save that already happened.

        The bytes are the source of truth. Naming an account that does not exist
        fails the frame write on its foreign key — the PUT must still report
        success, or the UI says "could not save" about a map that is on disk.
        """
        assert (
            client.put(
                "/map", params={"user_id": 999999}, json=_fc(_point(-122.68, 45.523, "shed"))
            ).status_code
            == 200
        )
        assert MAP_PATH.exists()

    async def test_a_save_without_an_account_is_refused(self, client):
        """`user_id` is required, not defaulted.

        Regression: every user-scoped endpoint took `user_id: int = 1`, which
        assumed an account with that id existed *and* that it was the one calling —
        so a request could be served as, or written to, the wrong household member.
        A request that cannot say who it is for has no correct answer.
        """
        assert client.raw.put("/map", json=_fc(_point(-122.68, 45.523))).status_code == 422

    async def test_a_second_map_is_a_separate_file(self, client, tmp_path):
        """Two maps are two files; the page names which one it is working on.

        The point of the picker: `map.geojson` was the only addressable map, so a
        second one could exist in the sandbox and the page could not open it.
        """
        client.put("/map", json=_fc(_point(-122.68, 45.523, "home")))
        client.put("/map", params={"file": "camping.geojson"},
                   json=_fc(_point(-121.5, 46.8, "campsite")))

        assert client.get("/map").json()["features"][0]["properties"]["description"] == "home"
        camping = client.get("/map", params={"file": "camping.geojson"}).json()
        assert camping["features"][0]["properties"]["description"] == "campsite"

    async def test_the_picker_lists_only_root_geojson(self, client, tmp_path):
        """What the picker offers: `.geojson`, at the root, and the default always."""
        client.put("/map", params={"file": "camping.geojson"}, json=_fc())
        # A nested one, and a non-geojson one, must not be offered.
        from assistant.backend.pipeline.filesystem import get_sandbox_root

        (get_sandbox_root() / "notes.txt").write_text("not a map")
        (get_sandbox_root() / "nested").mkdir(exist_ok=True)
        (get_sandbox_root() / "nested" / "deep.geojson").write_text("{}")

        files = client.get("/map/files").json()["files"]
        assert "camping.geojson" in files
        assert "map.geojson" in files, "the default is offered before it exists"
        assert "notes.txt" not in files
        assert "deep.geojson" not in files

    async def test_a_file_name_cannot_escape_the_sandbox(self, client):
        """Refused at the boundary, with a message that says which rule broke."""
        for bad in ("../secrets.geojson", "sub/dir.geojson", "map.json", ".hidden.geojson"):
            r = client.get("/map", params={"file": bad})
            assert r.status_code == 422, bad
            assert "geojson" in r.json()["detail"]

    async def test_put_makes_each_map_visible_in_the_files_list(self, client):
        """One file frame per map file, so each is a file the user can find."""
        client.put("/map", json=_fc())
        client.put("/map", params={"file": "camping.geojson"}, json=_fc())

        names = [e["file_name"] for e in client.get("/files/list").json()]
        assert "map.geojson" in names
        assert "camping.geojson" in names

    async def test_settings_carry_the_map_keys(self, client):
        s = client.get("/settings").json()
        assert "maps_enabled" in s
        assert "map_tiles_base_url" in s
        assert isinstance(s["map_styles"], list) and s["map_styles"]
        # The renderer draws labels with this font, and a glyph stack that is not
        # on the tile server's glyph endpoint has zero advance width — the label
        # comes out as unreadable overlapping text rather than as a missing font.
        assert all(style.get("label_font") for style in s["map_styles"])
