import csv
import json
import sys
from pathlib import Path

import pytest

from app.services.oasis_profile_generator import OasisAgentProfile, OasisProfileGenerator
from scripts import test_profile_format


@pytest.fixture
def profile():
    return OasisAgentProfile(
        user_id=7,
        user_name="alice_handle",
        name="Alice Example",
        bio="Alice bio",
        persona="Alice persona",
        karma=99,
        friend_count=11,
        follower_count=22,
        statuses_count=33,
        age=42,
        gender="female",
        mbti="ENFP",
        country="Canada",
        profession="Researcher",
        interested_topics=["AI", "Policy"],
        created_at="2024-01-02",
    )


def test_twitter_csv_has_oasis_header_order_and_profile_values(tmp_path, profile):
    path = tmp_path / "twitter_profiles.csv"

    OasisProfileGenerator.__new__(OasisProfileGenerator)._save_twitter_csv(
        [profile], str(path)
    )

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == [
            "user_id",
            "user_name",
            "name",
            "bio",
            "friend_count",
            "follower_count",
            "statuses_count",
            "created_at",
            "username", "user_char", "description",
        ]
        assert next(reader) == {
            "user_id": "7",
            "user_name": "alice_handle",
            "name": "Alice Example",
            "bio": "Alice bio",
            "friend_count": "11",
            "follower_count": "22",
            "statuses_count": "33",
            "created_at": "2024-01-02",
            "username": "alice_handle",
            "user_char": "Alice bio Alice persona",
            "description": "Alice bio",
        }


def test_reddit_json_maps_realname_and_preserves_persona_demographics(
    tmp_path, profile
):
    path = tmp_path / "reddit_profiles.json"

    OasisProfileGenerator.__new__(OasisProfileGenerator)._save_reddit_json(
        [profile], str(path)
    )

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == [
        {
            "user_id": 7,
            "realname": "Alice Example",
            "username": "alice_handle",
            "bio": "Alice bio",
            "persona": "Alice persona",
            "karma": 99,
            "created_at": "2024-01-02",
            "age": 42,
            "gender": "female",
            "mbti": "ENFP",
            "country": "Canada",
            "profession": "Researcher",
            "interested_topics": ["AI", "Policy"],
        }
    ]


def test_profile_serializers_support_empty_input(tmp_path):
    generator = OasisProfileGenerator.__new__(OasisProfileGenerator)
    twitter_path = tmp_path / "twitter_profiles.csv"
    reddit_path = tmp_path / "reddit_profiles.json"

    generator._save_twitter_csv([], str(twitter_path))
    generator._save_reddit_json([], str(reddit_path))

    with twitter_path.open(newline="", encoding="utf-8") as handle:
        assert next(csv.reader(handle)) == [
            "user_id",
            "user_name",
            "name",
            "bio",
            "friend_count",
            "follower_count",
            "statuses_count",
            "created_at",
            "username", "user_char", "description",
        ]
    assert json.loads(reddit_path.read_text(encoding="utf-8")) == []


def test_profile_verification_cli_returns_nonzero_on_contract_failure(monkeypatch):
    def fail_verification(*args, **kwargs):
        raise AssertionError("contract mismatch")

    monkeypatch.setattr(test_profile_format, "verify_profile_formats", fail_verification)

    assert test_profile_format.main([]) != 0
