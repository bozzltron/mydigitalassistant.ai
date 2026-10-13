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

    original_db_path = settings.database_path
    original_scheduler = settings.scheduler_enabled
    settings.database_path = store.db_path
    settings.scheduler_enabled = False
    try:
        with TestClient(app) as c:
            _state["llm_client"] = stub_llm
            _state["search_tool"] = stub_search
            yield c
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

    async def test_settings_carry_the_map_keys(self, client):
        s = client.get("/settings").json()
        assert "maps_enabled" in s
        assert "map_tiles_base_url" in s
        assert isinstance(s["map_styles"], list) and s["map_styles"]
