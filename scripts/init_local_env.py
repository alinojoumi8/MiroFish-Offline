#!/usr/bin/env python3
"""Create a private local .env and generate required secrets when absent."""

import argparse
import os
from pathlib import Path
import secrets
import tempfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GENERATED_KEYS = ("MIROFISH_CONTROL_TOKEN", "NEO4J_PASSWORD")


def _current_value(lines, key):
    prefix = f"{key}="
    for line in lines:
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def _set_value(lines, key, value):
    prefix = f"{key}="
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = f"{prefix}{value}"
            return
    lines.append(f"{prefix}{value}")


def initialize_env(template_path, env_path):
    source_path = env_path if env_path.exists() else template_path
    lines = source_path.read_text(encoding="utf-8").splitlines()

    for key in GENERATED_KEYS:
        if not _current_value(lines, key):
            _set_value(lines, key, secrets.token_hex(32))

    env_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=env_path.parent,
            prefix=".env.",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write("\n".join(lines) + "\n")
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, env_path)
        os.chmod(env_path, 0o600)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--template",
        type=Path,
        default=REPOSITORY_ROOT / ".env.example",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=REPOSITORY_ROOT / ".env",
    )
    args = parser.parse_args()

    initialize_env(args.template, args.env_file)
    print(f"Initialized private local environment at {args.env_file}")


if __name__ == "__main__":
    main()
