# Repository Guidelines

## Project Structure & Module Organization

MiroFish-Offline is split into a Python backend and a Vue frontend. Backend code lives in `backend/app/`, with Flask API routes in `backend/app/api/`, domain services in `backend/app/services/`, graph/embedding storage code in `backend/app/storage/`, and models in `backend/app/models/`. Backend utility scripts are in `backend/scripts/`; the current profile-format check is `backend/scripts/test_profile_format.py`. Frontend code lives in `frontend/src/`, with views in `frontend/src/views/`, reusable components in `frontend/src/components/`, API clients in `frontend/src/api/`, and router setup in `frontend/src/router/`. Shared static images and screenshots are in `static/image/`; frontend-served public assets are in `frontend/public/`.

## Build, Test, and Development Commands

- `npm run setup:all`: install root/frontend npm packages and sync backend dependencies with `uv`.
- `npm run dev`: run backend and frontend together with `concurrently`.
- `npm run backend`: start the backend via `cd backend && uv run python run.py`.
- `npm run frontend`: start the Vite dev server.
- `npm run build`: build the frontend for production.
- `docker compose up -d`: run the full local stack, including Neo4j and Ollama.
- `cd backend && uv run python scripts/test_profile_format.py`: run the current backend profile-format verification script.

## Coding Style & Naming Conventions

Use 4-space indentation for Python and 2-space indentation in Vue templates/styles. Python modules and functions use `snake_case`; classes use `PascalCase`. Keep backend service boundaries clear: route handlers should delegate graph, simulation, and reporting work to `services/` or `storage/`. Vue single-file components use `PascalCase` filenames, while API modules use lowercase names such as `frontend/src/api/report.js`. Existing JavaScript uses ES modules and single quotes.

## Testing Guidelines

Backend dev dependencies include `pytest` and `pytest-asyncio`; add new tests under `backend/tests/` as `test_*.py` when adding backend behavior. Keep script-style checks in `backend/scripts/` only when they validate external formats or manual workflows. No frontend test runner is currently configured, so verify UI changes with `npm run build` and a local Vite run.

## Commit & Pull Request Guidelines

Recent history uses concise subjects with Conventional Commit-style prefixes, for example `fix:` and `i18n:`. Prefer `type: imperative summary` and keep the first line focused. Pull requests should describe the behavior change, list verification commands, link related issues, note configuration or dependency changes, and include screenshots for visible frontend updates.

## Security & Configuration Tips

Copy `.env.example` to `.env` for local settings. Do not commit secrets, model API keys, Neo4j passwords, generated uploads, or local database volumes.
