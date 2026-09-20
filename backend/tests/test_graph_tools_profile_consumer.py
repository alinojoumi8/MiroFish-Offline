import csv

from app.config import Config
from app.services.graph_tools import GraphToolsService


SIMULATION_ID = "sim_0123456789ab"


def write_twitter_profiles(path, headers, row):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerow(row)


def test_interview_profile_loader_consumes_oasis_twitter_contract(tmp_path, monkeypatch):
    simulation_dir = tmp_path / SIMULATION_ID
    simulation_dir.mkdir()
    write_twitter_profiles(
        simulation_dir / "twitter_profiles.csv",
        [
            "user_id",
            "user_name",
            "name",
            "bio",
            "friend_count",
            "follower_count",
            "statuses_count",
            "created_at",
        ],
        [7, "alice_handle", "Alice Example", "Alice bio", 11, 22, 33, "2024-01-02"],
    )
    monkeypatch.setattr(Config, "OASIS_SIMULATION_DATA_DIR", str(tmp_path))

    profiles = GraphToolsService.__new__(GraphToolsService)._load_agent_profiles(
        SIMULATION_ID
    )

    assert profiles == [
        {
            "realname": "Alice Example",
            "username": "alice_handle",
            "bio": "Alice bio",
            "persona": "Alice bio",
            "profession": "Unknown",
        }
    ]


def test_interview_profile_loader_retains_legacy_twitter_columns(tmp_path, monkeypatch):
    simulation_dir = tmp_path / SIMULATION_ID
    simulation_dir.mkdir()
    write_twitter_profiles(
        simulation_dir / "twitter_profiles.csv",
        ["user_id", "name", "username", "user_char", "description"],
        [7, "Legacy Alice", "legacy_alice", "Legacy persona", "Legacy bio"],
    )
    monkeypatch.setattr(Config, "OASIS_SIMULATION_DATA_DIR", str(tmp_path))

    profiles = GraphToolsService.__new__(GraphToolsService)._load_agent_profiles(
        SIMULATION_ID
    )

    assert profiles == [
        {
            "realname": "Legacy Alice",
            "username": "legacy_alice",
            "bio": "Legacy bio",
            "persona": "Legacy persona",
            "profession": "Unknown",
        }
    ]
