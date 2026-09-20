"""Client-safe failure messages and correlation IDs for persisted state."""

import uuid


OPERATION_FAILURE_MESSAGE = 'Operation failed; check server logs'
TASK_FAILURE_MESSAGE = 'Task failed; check server logs'


def new_error_request_id():
    return str(uuid.uuid4())
