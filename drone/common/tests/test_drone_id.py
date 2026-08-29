"""The drone's identity has to survive a restart.

It is what the app registers, what the IoT Thing is named, and what every MQTT
topic embeds. A drone that mints a new ID each boot orphans its registration
every time and cannot be controlled from the app - and it does so silently,
which is why it went unnoticed: the fallback file was written when /etc was not
writable (the normal case, since the service is not root) and never read back.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import provisioning  # noqa: E402


@pytest.fixture
def id_files(tmp_path, monkeypatch):
    """Redirect both ID locations into tmp_path. Returns (etc, fallback)."""
    etc = tmp_path / "etc-drone-id"
    fallback = tmp_path / "fallback-drone-id"
    monkeypatch.setattr(provisioning, "DRONE_ID_FILE", str(etc))
    monkeypatch.setattr(provisioning, "DRONE_ID_FALLBACK_FILE", fallback)
    return etc, fallback


def test_id_survives_a_restart_when_only_the_fallback_is_writable(id_files, monkeypatch):
    """The regression: /etc unwritable, so the ID lands in the fallback. A second
    call is the next boot, and it must return the same ID rather than mint one."""
    etc, fallback = id_files
    monkeypatch.setattr(provisioning, "DRONE_ID_FILE", "/proc/definitely-not-writable")

    first = provisioning.get_or_create_drone_id()
    assert fallback.read_text().strip() == first

    second = provisioning.get_or_create_drone_id()
    assert second == first, "drone minted a new ID on restart; registration is orphaned"


def test_etc_wins_over_the_fallback(id_files):
    etc, fallback = id_files
    etc.write_text("drone-fromtheetcfile\n")
    fallback.write_text("drone-fromthefallback\n")
    assert provisioning.get_or_create_drone_id() == "drone-fromtheetcfile"


def test_config_pins_the_id(id_files):
    """config.yaml's drone_id is documented in three places and used to be ignored."""
    etc, _ = id_files
    etc.write_text("drone-persisted\n")
    assert provisioning.get_or_create_drone_id("drone-pinned") == "drone-pinned"


def test_blank_config_value_does_not_win(id_files):
    """A commented-out or empty key must not blank the identity."""
    etc, _ = id_files
    etc.write_text("drone-persisted\n")
    for empty in ("", "   ", None):
        assert provisioning.get_or_create_drone_id(empty) == "drone-persisted"


def test_config_override_of_a_different_stored_id_is_warned(id_files, caplog):
    """A config copied from another drone puts two aircraft on one command topic."""
    etc, _ = id_files
    etc.write_text("drone-thisdevice\n")
    with caplog.at_level("WARNING"):
        provisioning.get_or_create_drone_id("drone-otherdevice")
    assert any("same command topic" in r.getMessage() for r in caplog.records)


def test_an_empty_id_file_is_not_an_identity(id_files):
    """read().strip() on an empty file returned "", which would publish to drone//..."""
    etc, _ = id_files
    etc.write_text("   \n")
    generated = provisioning.get_or_create_drone_id()
    assert generated.startswith("drone-") and len(generated) > len("drone-")


def test_generated_id_is_persisted_and_reused(id_files):
    etc, _ = id_files
    first = provisioning.get_or_create_drone_id()
    assert etc.read_text().strip() == first
    assert provisioning.get_or_create_drone_id() == first


def test_unpersistable_id_is_reported_not_swallowed(id_files, monkeypatch, caplog):
    """If neither location can be written the ID will churn; that must be loud."""
    monkeypatch.setattr(provisioning, "DRONE_ID_FILE", "/proc/nope")
    monkeypatch.setattr(provisioning, "DRONE_ID_FALLBACK_FILE", Path("/proc/also-nope"))
    with caplog.at_level("ERROR"):
        drone_id = provisioning.get_or_create_drone_id()
    assert drone_id.startswith("drone-")
    assert any("could not persist" in r.getMessage().lower() for r in caplog.records)
