# Repository Guidelines

## Project Structure & Module Organization

`mediZJ/` contains the Python application. Orchestration lives in `core/`, Swarm coordination in `swarm/`, LangGraph workflows in `lgraph/` (including the lightweight `Worker` spec that replaces the former `agents/` package), API routes and services in `api/`, persistence in `memory/`, retrieval in `knowledge/`, self-improvement in `evolution/` (feedback-driven evaluation and reusable experience extraction), and Jinja prompts in `prompt/`. Backend tests mirror these areas under `tests/`. The Vue 3 client is in `frontend/src/`, organized into `components/`, `views/`, `stores/`, `composables/`, `api/`, `router/`, `types/`, and `utils/`; colocated frontend tests use `__tests__/`. Documentation belongs in `docs/`, while utilities live in `scripts/`.

## Build, Test, and Development Commands

- `uv sync --extra dev`: install Python runtime and development dependencies.
- `uv run python mediZJ/api_main.py`: start the FastAPI backend locally.
- `uv run pytest`: run the backend test suite with configured strict markers.
- `uv run pytest --cov=mediZJ --cov-report=term-missing`: measure backend coverage.
- `uv run ruff check .` and `uv run mypy mediZJ`: run Python linting and type checks.
- `cd frontend && npm install`: install locked frontend dependencies.
- `cd frontend && npm run dev`: start the Vite development server.
- `cd frontend && npm run build`: type-check and build the production client.
- `cd frontend && npm run lint && npm test`: lint and run Vitest once.

## Coding Style & Naming Conventions

Follow PEP 8 with four-space indentation for Python. Use `snake_case` for modules, functions, and variables; `PascalCase` for classes; and type annotations on public interfaces. Keep prompts in `.j2` templates rather than embedding long strings. For Vue and TypeScript, use Prettier and ESLint; name components `PascalCase.vue`, composables `useFeature.ts`, and stores by domain. Do not commit generated `dist/`, caches, logs, databases, or secrets.

## Testing Guidelines

Pytest discovers `test_*.py`, `Test*`, and `test_*`; mark external-service tests as `integration` and real-LLM tests as `slow`. Keep unit tests deterministic and mock LLM, Milvus, Redis, and network boundaries. Frontend specs use `*.spec.ts`. New or changed behavior should target at least 80% coverage and include failure, concurrency, and boundary cases where relevant.

## Commit & Pull Request Guidelines

Use Angular-style commits seen in history, such as `feat(concurrency): isolate user profiles` or `fix(agent-loop): prevent tool-limit loops`. Keep each commit focused. Pull requests should summarize behavior changes, list verification commands, link related issues, call out configuration or migration impacts, and include screenshots for UI changes.

## Security & Configuration

Copy `.env.example` to `.env` for local setup. Never commit API keys, credentials, patient data, or production endpoints. Preserve medical safety warnings and validation constraints when changing prompts or agent routing.
