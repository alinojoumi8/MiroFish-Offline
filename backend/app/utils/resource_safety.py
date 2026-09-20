"""Validation and containment helpers for filesystem-backed resources."""

import re
from pathlib import Path
from typing import Union


class ResourceValidationError(ValueError):
    """Raised when an external resource identifier or path is unsafe."""


PROJECT_ID_PATTERN = re.compile(r'proj_[0-9a-f]{12}\Z')
SIMULATION_ID_PATTERN = re.compile(r'sim_[0-9a-f]{12}\Z')
REPORT_ID_PATTERN = re.compile(r'report_[0-9a-f]{12}\Z')
UUID_PATTERN = re.compile(
    r'[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z',
    re.IGNORECASE,
)
SCRIPT_ALLOWLIST = frozenset({
    'run_twitter_simulation.py',
    'run_reddit_simulation.py',
    'run_parallel_simulation.py',
    'action_logger.py',
})


def _validate(value: str, pattern: re.Pattern, resource_name: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ResourceValidationError(f'Invalid {resource_name}')
    return value


def validate_project_id(project_id: str) -> str:
    return _validate(project_id, PROJECT_ID_PATTERN, 'project ID')


def validate_simulation_id(simulation_id: str) -> str:
    return _validate(simulation_id, SIMULATION_ID_PATTERN, 'simulation ID')


def validate_report_id(report_id: str) -> str:
    return _validate(report_id, REPORT_ID_PATTERN, 'report ID')


def validate_task_id(task_id: str) -> str:
    return _validate(task_id, UUID_PATTERN, 'task ID')


def validate_graph_id(graph_id: str) -> str:
    return _validate(graph_id, UUID_PATTERN, 'graph ID')


def validate_script_name(script_name: str) -> str:
    if not isinstance(script_name, str) or script_name not in SCRIPT_ALLOWLIST:
        raise ResourceValidationError('Invalid script name')
    return script_name


def resolve_resource_path(
    storage_root: Union[str, Path],
    *parts: Union[str, Path],
    allow_root: bool = False,
) -> str:
    """Resolve a path only when it remains beneath a storage root."""
    root = Path(storage_root).resolve(strict=False)
    candidate = root
    for part in parts:
        part_path = Path(part)
        if part_path.is_absolute() or '..' in part_path.parts:
            raise ResourceValidationError('Unsafe resource path')
        candidate /= part_path

    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ResourceValidationError('Resource path escapes storage root') from exc
    if not allow_root and resolved == root:
        raise ResourceValidationError('Storage root is not a resource')
    return str(resolved)
