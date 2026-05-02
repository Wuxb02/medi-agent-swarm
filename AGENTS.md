# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python 3.10+ multi-agent medical assistant system. Core orchestration code lives in `core/`, including agent loops, LLM clients, skill loading, and state management. Agent implementations are in `agents/`, while swarm coordination is in `swarm/`. Medical knowledge integrations belong in `knowledge/`; memory components are split under `memory/`; constraint and validation logic lives in `constraints/` and `validation/`. Research workflows and web/evidence utilities are in `research/`. The main local entry point is `main.py`, and the current end-to-end test runner is `examples/test_all.py`.

## Build, Test, and Development Commands

- `uv sync`: install dependencies from `pyproject.toml` and `uv.lock` when using `uv`.
- `pip install -e .`: install the project in editable mode for local development.
- `cp .env.example .env`: create local configuration before running the app.
- `python main.py`: start the interactive medical assistant.
- `python examples/test_all.py`: run the existing integration-style test suite.

`setup.py` expects `requirements.txt`, but dependencies are currently defined in `pyproject.toml`; prefer `uv sync` unless a requirements file is restored.

## Coding Style & Naming Conventions

Follow PEP 8 with 4-space indentation, clear function names, and small modules. Use `snake_case` for functions, variables, and module files; use `PascalCase` for classes; keep constants in `UPPER_SNAKE_CASE`. Add concise Chinese comments only where they clarify non-obvious medical, agent, or safety logic. Keep implementations simple and avoid broad refactors unrelated to the current change.

## Testing Guidelines

Use `python examples/test_all.py` before submitting changes that affect agents, skills, memory, constraints, or swarm behavior. New tests should use descriptive names such as `test_agent_loop_context_usage` or `test_constraint_validation_failure`. Aim for at least 80% meaningful coverage for changed logic, especially safety validation, memory state, and tool/skill execution paths. Include edge cases for missing configuration, failed LLM responses, and empty knowledge-base results.

## Commit & Pull Request Guidelines

Git history uses Angular-style commits, for example `refactor(config): 迁移配置管理从 config.py 到 .env 文件`. Use the format `<type>(<scope>): <subject>`, such as `feat(agent): add consultation retry handling` or `fix(memory): handle empty session history`. Pull requests should include a concise summary, testing evidence, linked issues when available, and screenshots or logs for user-visible CLI behavior. Note any `.env` or external service requirements.

## Security & Configuration Tips

Never commit `.env`, API keys, patient data, or generated private logs. Keep medical outputs safety-aware: preserve validation checks, avoid unsupported diagnosis claims, and document any new external API dependency in code or configuration examples only when requested.
