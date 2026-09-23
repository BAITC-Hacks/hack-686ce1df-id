# Submission readiness implementation plan

**Goal:** Complete the five agreed submission tasks, in order: CSV compatibility, local secrets, reproducible startup, handoff package, demonstration.

**Architecture:** Preserve the analytics and HTTP contracts. Extend only the task CSV, use environment interpolation for the existing AI configuration, and add an explicit Docker workflow for real Parquet input. Keep raw inputs, results and credentials outside Git.

**Tech stack:** Python 3.12, NetworkX/NumPy, FastAPI, Docker Compose, existing React build.

**Spec:** The five-item checklist approved in conversation; `data/reference/starter/README.md` and `starter.py` define the supplied export shape. Execute sequentially in this task; independent review and checks may run within a step.

## Constraints and review focus

- Preserve exact string identifiers and exact money; CSV scores remain 0–1.
- Keep all 2248 nodes, including isolates. Undefined ratios are explicitly missing, never silently zero.
- Do not print or commit credentials; preserve the user's selected model and working key.
- A real-data launch must fail clearly on missing Parquet, never silently serve fixtures.
- Package only an explicit allowlist; verify ZIP contents and hashes, excluding inputs and secrets.
- Live AI is user-confirmed; don't describe mock/fallback checks as a live-provider test.

## 1. Full CSV schema

- [x] Extend `backend/app/analytics/exports.py` with starter's nine metric/quality columns; calculate deterministic amount-weighted directed PageRank over all nodes.
- [x] Add meaningful assertions in `tests/analytics/test_pipeline.py` and focused export tests for the literal starter headers, zero incoming amounts, direction/weights and isolates.
- [x] Run `.venv/bin/python -m pytest tests/analytics -q`, then two fresh real calculations. Compare the 13 substantive artifacts and check all three CSVs against Parquet/JSON values.
- [x] Sync the verified change into the user's local checkout and serve the newly calculated immutable run. Verify `scripts/real_smoke.py` on port 8000.

## 2. Local API-key storage

- [x] Change `compose.yaml` to read `OPENAI_API_KEY` and `AI_MODEL` through environment interpolation with empty defaults. Keep `.env.example` free of real values.
- [x] In the user's local checkout, move the existing literal key/model to ignored `.env`, mode 0600, without logging their values. Replace tracked files with the reviewed templates.
- [x] Recreate only the local app. Compare the effective key/model in memory and scan tracked changes for literal credentials; print booleans only.

## 3. Reproducible real-data startup

- [x] Add `compose.real.yaml`: read-only Parquet mount, writable named artifacts volume, explicit `--data-dir`, fixtures disabled, healthcheck retained.
- [x] Update `README.md`, `docs/docker.md`, `.env.example` and `docs/ai.md` with one clean-machine Docker command, explicit fixture command, AI setup and rollback. Mark historical verification reports as historical.
- [x] Test a fresh temporary Compose project/volume and separate port with AI disabled; validate all real-data HTTP exports and missing-input failure. Remove only owned temporary containers/volumes.

## 4. Submission package

- [x] Assemble an ignored local package containing the three final CSVs, methodology, launch instructions, run manifest, rules/mapping, validation report and SHA256 checksums.
- [x] Include source code from an explicit Git-derived allowlist; exclude `.env`, archives/raw data, virtualenvs, dependencies, caches and old artifacts.
- [x] Create a ZIP and inspect entries, hashes, row counts and secret scan. Report its absolute path.

## 5. Demonstration

- [x] Write `docs/demo-script.md` using verified actual gids: a ranked node, a boundary node and an isolate; include a cluster and AI question, expected observations and fallback plan.
- [x] Manually verify the scenario in the current UI; don't invoke a paid provider merely to re-confirm user-reported success.
- [x] Run final relevant tests, review the complete change and synchronize the user's checkout. Deliver all five outcomes and any remaining external CI limitation.
