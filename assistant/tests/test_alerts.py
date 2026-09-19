"""Tests for the alerts (learning monitor) system."""

import asyncio

import pytest
import pytest_asyncio

from assistant.backend.db.schema import init_db
from assistant.backend.memory.store import MemoryStore


@pytest_asyncio.fixture
async def store(tmp_path):
    """Create a fresh MemoryStore with initialized schema for each test."""
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    s = MemoryStore(db_path)
    # Create default users for tests
    await s.create_user("test_user")
    await s.create_user("test_user_2")
    return s


@pytest.mark.asyncio
async def test_create_alert(store: MemoryStore):
    """Test creating a basic alert."""
    alert = await store.create_alert(
        user_id=1,
        type="learning",
        title="Test Alert",
        message="This is a test alert message",
        severity="info",
    )
    
    assert alert.id is not None
    assert alert.user_id == 1
    assert alert.type == "learning"
    assert alert.title == "Test Alert"
    assert alert.message == "This is a test alert message"
    assert alert.severity == "info"
    assert alert.is_read is False
    assert alert.created_at is not None


@pytest.mark.asyncio
async def test_get_alerts(store: MemoryStore):
    """Test fetching alerts for a user."""
    # Create multiple alerts
    await store.create_alert(
        user_id=1, type="learning", title="Alert 1", message="Msg 1"
    )
    await store.create_alert(
        user_id=1, type="task_result", title="Alert 2", message="Msg 2"
    )
    await store.create_alert(
        user_id=2, type="learning", title="Alert 3", message="Msg 3"
    )
    
    # Get alerts for user 1
    alerts = await store.get_alerts(user_id=1, limit=10)
    
    assert len(alerts) == 2
    # Newest first
    assert alerts[0].title == "Alert 2"
    assert alerts[1].title == "Alert 1"


@pytest.mark.asyncio
async def test_get_unread_alert_count(store: MemoryStore):
    """Test getting unread alert count."""
    await store.create_alert(user_id=1, type="learning", title="Alert 1", message="Msg 1")
    await store.create_alert(user_id=1, type="learning", title="Alert 2", message="Msg 2")
    
    count = await store.get_unread_alert_count(user_id=1)
    assert count == 2


@pytest.mark.asyncio
async def test_mark_alert_read(store: MemoryStore):
    """Test marking an alert as read."""
    alert = await store.create_alert(user_id=1, type="learning", title="Alert 1", message="Msg 1")
    
    # Mark as read
    success = await store.mark_alert_read(alert.id, 1)
    assert success is True
    
    # Verify
    alerts = await store.get_alerts(user_id=1)
    assert len(alerts) == 1
    assert alerts[0].is_read is True
    assert alerts[0].read_at is not None
    
    # Count should be 0
    count = await store.get_unread_alert_count(user_id=1)
    assert count == 0


@pytest.mark.asyncio
async def test_mark_alert_read_wrong_user(store: MemoryStore):
    """Test that marking alert read fails for wrong user."""
    alert = await store.create_alert(user_id=1, type="learning", title="Alert 1", message="Msg 1")
    
    # Try to mark as read by different user
    success = await store.mark_alert_read(alert.id, 2)
    assert success is False
    
    # Alert should still be unread
    alerts = await store.get_alerts(user_id=1)
    assert alerts[0].is_read is False


@pytest.mark.asyncio
async def test_mark_all_alerts_read(store: MemoryStore):
    """Test marking all alerts as read."""
    await store.create_alert(user_id=1, type="learning", title="Alert 1", message="Msg 1")
    await store.create_alert(user_id=1, type="learning", title="Alert 2", message="Msg 2")
    await store.create_alert(user_id=2, type="learning", title="Alert 3", message="Msg 3")
    
    count = await store.mark_all_alerts_read(user_id=1)
    assert count == 2
    
    # User 1 should have no unread
    unread = await store.get_unread_alert_count(user_id=1)
    assert unread == 0
    
    # User 2 should still have 1 unread
    unread2 = await store.get_unread_alert_count(user_id=2)
    assert unread2 == 1


@pytest.mark.asyncio
async def test_alert_with_source_frame_and_episode(store: MemoryStore):
    """Test creating alert with source frame and episode references."""
    # First create a frame with owner
    frame = await store.create_frame("test_frame", "entity", owner_user_id=1)
    
    # Create alert with source references (no episode to avoid FK issues)
    alert = await store.create_alert(
        user_id=1,
        type="conflict",
        title="Conflict Alert",
        message="A conflict was resolved",
        source_frame_id=frame.id,
        severity="warning",
    )
    
    assert alert.source_frame_id == frame.id
    assert alert.source_episode_id is None
    assert alert.severity == "warning"


@pytest.mark.asyncio
async def test_alert_severities(store: MemoryStore):
    """Test different alert severities."""
    await store.create_alert(
        user_id=1, type="learning", title="Info", message="Info", severity="info"
    )
    await store.create_alert(
        user_id=1, type="learning", title="Warning", message="Warning", severity="warning"
    )
    await store.create_alert(
        user_id=1,
        type="learning",
        title="Important",
        message="Important",
        severity="important",
    )
    
    alerts = await store.get_alerts(user_id=1)
    assert len(alerts) == 3
    
    severities = {a.severity for a in alerts}
    assert severities == {"info", "warning", "important"}


@pytest.mark.asyncio
async def test_alert_types(store: MemoryStore):
    """Test different alert types."""
    types = ["learning", "task_result", "search_result", "conflict", "correction"]
    
    for t in types:
        await store.create_alert(user_id=1, type=t, title=f"{t} alert", message=f"Message for {t}")
    
    alerts = await store.get_alerts(user_id=1)
    assert len(alerts) == 5
    
    alert_types = {a.type for a in alerts}
    assert alert_types == set(types)


@pytest.mark.asyncio
async def test_get_alerts_unread_only(store: MemoryStore):
    """Test getting only unread alerts."""
    await store.create_alert(user_id=1, type="learning", title="Alert 1", message="Msg 1")
    await asyncio.sleep(0.01)  # Ensure different timestamps
    await store.create_alert(user_id=1, type="learning", title="Alert 2", message="Msg 2")
    
    # Mark newest as read
    alerts = await store.get_alerts(user_id=1)
    await store.mark_alert_read(alerts[0].id, 1)
    
    # Get unread only
    unread = await store.get_alerts(user_id=1, unread_only=True)
    assert len(unread) == 1
    assert unread[0].title == "Alert 1"  # Oldest unread


@pytest.mark.asyncio
async def test_alert_limit(store: MemoryStore):
    """Test alert limit parameter."""
    for i in range(10):
        await store.create_alert(user_id=1, type="learning", title=f"Alert {i}", message=f"Msg {i}")
        await asyncio.sleep(0.01)  # Ensure different timestamps
    
    alerts = await store.get_alerts(user_id=1, limit=3)
    assert len(alerts) == 3
    
    # Should be newest 3 (Alert 9, 8, 7)
    assert alerts[0].title == "Alert 9"
    assert alerts[2].title == "Alert 7"