"""
API Routes Module
"""

from flask import Blueprint, current_app, jsonify, request

from ..utils.resource_safety import (
    ResourceValidationError,
    validate_graph_id,
    validate_project_id,
    validate_report_id,
    validate_simulation_id,
    validate_task_id,
)

graph_bp = Blueprint('graph', __name__)
simulation_bp = Blueprint('simulation', __name__)
report_bp = Blueprint('report', __name__)

_RESOURCE_VALIDATORS = {
    'project_id': validate_project_id,
    'simulation_id': validate_simulation_id,
    'report_id': validate_report_id,
    'task_id': validate_task_id,
    'graph_id': validate_graph_id,
    'entity_uuid': validate_graph_id,
}


def _validate_resource_inputs():
    """Reject malformed IDs before a route can use them as resource handles."""
    if not current_app.config.get('STRICT_RESOURCE_ID_VALIDATION', False):
        return None
    values = dict(request.view_args or {})
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        values.update({key: value for key, value in data.items() if key in _RESOURCE_VALIDATORS})
    values.update({
        key: request.args[key]
        for key in _RESOURCE_VALIDATORS
        if key in request.args
    })
    for key, validator in _RESOURCE_VALIDATORS.items():
        if key in values and values[key] is not None:
            try:
                validator(values[key])
            except ResourceValidationError as exc:
                return jsonify({'success': False, 'error': str(exc)}), 400
    return None


for _blueprint in (graph_bp, simulation_bp, report_bp):
    _blueprint.before_request(_validate_resource_inputs)

from . import graph  # noqa: E402, F401
from . import simulation  # noqa: E402, F401
from . import report  # noqa: E402, F401
