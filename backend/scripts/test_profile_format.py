"""Executable checks for the OASIS 0.2.5 profile file contract."""

import csv
import json
import os
import sys
import tempfile
from typing import Iterable

# Add project path when run as ``python backend/scripts/test_profile_format.py``.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.oasis_profile_generator import OasisAgentProfile, OasisProfileGenerator


TWITTER_FIELDS = [
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
REDDIT_REQUIRED_FIELDS = [
    "user_id",
    "realname",
    "username",
    "bio",
    "persona",
    "karma",
    "created_at",
    "age",
    "gender",
    "mbti",
    "country",
]


def build_test_profiles() -> list[OasisAgentProfile]:
    return [
        OasisAgentProfile(
            user_id=0,
            user_name="test_user_123",
            name="Test User",
            bio="A test user for validation",
            persona="Test User is an enthusiastic participant in social discussions.",
            karma=1500,
            friend_count=100,
            follower_count=200,
            statuses_count=500,
            age=25,
            gender="male",
            mbti="INTJ",
            country="China",
            profession="Student",
            interested_topics=["Technology", "Education"],
            source_entity_uuid="test-uuid-123",
            source_entity_type="Student",
        ),
        OasisAgentProfile(
            user_id=1,
            user_name="org_official_456",
            name="Official Organization",
            bio="Official account for Organization",
            persona="This is an official institutional account that communicates official positions.",
            karma=5000,
            friend_count=50,
            follower_count=10000,
            statuses_count=200,
            profession="Organization",
            interested_topics=["Public Policy", "Announcements"],
            source_entity_uuid="test-uuid-456",
            source_entity_type="University",
        ),
    ]


def assert_twitter_csv_contract(file_path: str) -> list[dict[str, str]]:
    with open(file_path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == TWITTER_FIELDS, (
            f"Twitter header mismatch: {reader.fieldnames!r}"
        )
        rows = list(reader)
    for row in rows:
        assert list(row) == TWITTER_FIELDS
    return rows


def assert_reddit_json_contract(file_path: str) -> list[dict]:
    with open(file_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    assert isinstance(data, list), "Reddit profile payload must be a list"
    for profile in data:
        missing = [field for field in REDDIT_REQUIRED_FIELDS if field not in profile]
        assert not missing, f"Reddit profile missing fields: {missing}"
        assert "name" not in profile, "Reddit contract uses realname, not name"
    return data


def verify_profile_formats(profiles: Iterable[OasisAgentProfile] | None = None) -> None:
    profiles = list(build_test_profiles() if profiles is None else profiles)
    generator = OasisProfileGenerator.__new__(OasisProfileGenerator)
    with tempfile.TemporaryDirectory() as temp_dir:
        twitter_path = os.path.join(temp_dir, "twitter_profiles.csv")
        reddit_path = os.path.join(temp_dir, "reddit_profiles.json")
        generator._save_twitter_csv(profiles, twitter_path)
        generator._save_reddit_json(profiles, reddit_path)
        twitter_rows = assert_twitter_csv_contract(twitter_path)
        reddit_data = assert_reddit_json_contract(reddit_path)
        assert len(twitter_rows) == len(profiles)
        assert len(reddit_data) == len(profiles)
        for profile, row, reddit_profile in zip(profiles, twitter_rows, reddit_data):
            assert row["user_id"] == str(profile.user_id)
            assert row["user_name"] == profile.user_name
            assert row["name"] == profile.name
            assert row["bio"] == profile.bio
            assert row["friend_count"] == str(profile.friend_count)
            assert row["follower_count"] == str(profile.follower_count)
            assert row["statuses_count"] == str(profile.statuses_count)
            assert reddit_profile["realname"] == profile.name
            assert reddit_profile["persona"] == profile.persona
            assert reddit_profile["age"] == (profile.age or 30)
            assert reddit_profile["gender"] == (profile.gender or "other")
            assert reddit_profile["mbti"] == (profile.mbti or "ISTJ")
            assert reddit_profile["country"] == (profile.country or "US")


def test_profile_formats() -> None:
    """Pytest-compatible entry point retained for the standalone check."""
    verify_profile_formats()


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        verify_profile_formats()
    except (AssertionError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"OASIS profile contract FAILED: {exc}", file=sys.stderr)
        return 1
    print("OASIS profile contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
