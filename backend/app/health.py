"""Liveness and dependency-aware readiness checks."""

import os
import shutil
import tempfile
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from urllib.parse import urljoin

import requests
from flask import jsonify

from .storage import EmbeddingService


def _safe_check(check):
    try:
        result = check()
        if isinstance(result, tuple) and len(result) == 2:
            ready, detail = result
            return bool(ready), str(detail)
        return bool(result), "ok" if result else "unavailable"
    except Exception:
        return False, "unavailable"


def _default_checks(app):
    timeout = float(app.config.get("READINESS_TIMEOUT_SECONDS", 2.0))

    def neo4j():
        storage = app.extensions.get("neo4j_storage")
        driver = getattr(storage, "_driver", None)
        if driver is None:
            return False, "unavailable"
        driver.verify_connectivity()
        return True, "connected"

    def llm():
        base_url = str(app.config.get("LLM_BASE_URL", "")).rstrip("/") + "/"
        response = requests.get(urljoin(base_url, "models"), timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        configured = app.config.get("LLM_MODEL_NAME")
        models = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(models, list) or not models:
            return False, "configured model unavailable"
        model_ids = {
            item.get("id") for item in models if isinstance(item, dict) and item.get("id")
        }
        if not model_ids or (configured and configured not in model_ids):
            return False, "configured model unavailable"
        return True, "models endpoint reachable"

    def embedding():
        service = EmbeddingService(
            model=app.config.get("EMBEDDING_MODEL"),
            base_url=app.config.get("EMBEDDING_BASE_URL"),
            max_retries=1,
            timeout=max(1, int(timeout)),
        )
        reachable = service.health_check()
        return reachable, "reachable" if reachable else "unavailable"

    def uploads():
        upload_folder = Path(app.config["UPLOAD_FOLDER"])
        upload_folder.mkdir(parents=True, exist_ok=True)
        descriptor, probe = tempfile.mkstemp(prefix=".readiness-", dir=str(upload_folder))
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(b"ready")
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            try:
                os.unlink(probe)
            except FileNotFoundError:
                pass
        return True, "writable"

    def disk():
        upload_folder = Path(app.config["UPLOAD_FOLDER"])
        upload_folder.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(upload_folder).free
        minimum = int(app.config.get("READINESS_MIN_FREE_DISK_BYTES", 0))
        if free < minimum:
            return False, "free disk below configured threshold"
        return True, "sufficient free disk"

    return {
        "neo4j": neo4j,
        "llm": llm,
        "embedding": embedding,
        "uploads": uploads,
        "disk": disk,
    }


def readiness_payload(app):
    checks = app.extensions["readiness_checks"]
    cache = app.extensions["readiness_cache"]
    ttl = float(app.config.get("READINESS_CACHE_TTL_SECONDS", 1.0))
    timeout = float(app.config.get("READINESS_TIMEOUT_SECONDS", 2.0))
    now = time.monotonic()
    with cache["lock"]:
        retired = cache["retired"]
        if retired is not None and all(
            future.done() for future in retired["futures"].values()
        ):
            cache["retired"] = None
        if (
            cache["payload"] is not None
            and cache["checks_id"] == id(checks)
            and now - cache["recorded_at"] < ttl
        ):
            return cache["payload"], cache["status"]
        inflight = cache["inflight"]
        inflight_done = inflight is not None and all(
            future.done() for future in inflight["futures"].values()
        )
        inflight_expired = (
            inflight is not None
            and now - inflight["started_at"] >= timeout
            and not inflight_done
        )
        needs_replacement = (
            inflight is None
            or inflight["checks_id"] != id(checks)
            or inflight_expired
        )
        if needs_replacement and inflight is not None and not inflight_done:
            if cache["retired"] is None:
                cache["retired"] = inflight
                inflight = None
            else:
                needs_replacement = False
        if needs_replacement:
            inflight = {
                "checks_id": id(checks),
                "started_at": now,
                "futures": {
                    name: cache["executor"].submit(_safe_check, check)
                    for name, check in checks.items()
                },
            }
            cache["inflight"] = inflight

    futures = inflight["futures"]
    remaining = max(0.0, inflight["started_at"] + timeout - time.monotonic())
    if futures and remaining > 0:
        wait(tuple(futures.values()), timeout=remaining)

    components = {}
    for name, future in futures.items():
        ready, detail = future.result() if future.done() else (False, "timeout")
        components[name] = {"ready": ready, "detail": detail}
    all_ready = all(component["ready"] for component in components.values())
    payload = {
        "status": "ready" if all_ready else "not_ready",
        "components": components,
    }
    status = 200 if all_ready else 503
    if all(future.done() for future in futures.values()):
        with cache["lock"]:
            if cache["inflight"] is inflight:
                cache.update(
                    payload=payload,
                    status=status,
                    checks_id=id(checks),
                    recorded_at=time.monotonic(),
                    inflight=None,
                )
    return payload, status


def install_health_routes(app):
    executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="readiness")
    weakref.finalize(
        app,
        executor.shutdown,
        wait=False,
        cancel_futures=True,
    )
    app.extensions["readiness_checks"] = _default_checks(app)
    app.extensions["readiness_cache"] = {
        "lock": threading.Lock(),
        "executor": executor,
        "inflight": None,
        "retired": None,
        "payload": None,
        "status": None,
        "checks_id": None,
        "recorded_at": 0.0,
    }

    @app.get("/health")
    @app.get("/health/live")
    def health_live():
        return jsonify({"status": "live", "service": "MiroFish-Offline Backend"})

    @app.get("/health/ready")
    def health_ready():
        payload, status = readiness_payload(app)
        return jsonify(payload), status

    @app.get("/api/status")
    def api_status():
        payload, status = readiness_payload(app)
        return jsonify(payload), status
