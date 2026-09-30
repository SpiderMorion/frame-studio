# Source-publication verification

This repository is a new-history source snapshot of internal baseline `c71b51f82c6d2eea256ee8895ce8eb187d684f3c`. The uncommitted Astral work is not included. Publication changes are documentation, CI, ignore rules, portable service templates, frontend ESLint configuration, CTest registration for two host projects, and replacement of site-specific backend address defaults/placeholders. No baseline source files were removed; the complete converter and display-driver component are included.

## Local checks on the final source candidate

- Python 3.12: `pytest -q` — **224 passed, 2 skipped**. The two PostgreSQL integration tests require a dedicated disposable database and were not run; no production database was used.
- `ruff check src tests migrations` — passed; `mypy` — passed (18 source files).
- Frontend: `npm test` — **39 passed**; `npm run lint` and `npm run build` — passed.
- `uv build --wheel` — passed.
- Host firmware: core, device-state, network-policy, frame-store planner/journal, provisioning and backend manifest parser — all passed. The backend parser test used ESP-IDF cJSON.
- Full factory application: **ESP-IDF v5.5.5 / Python 3.12 / esp32s3 build passed**, including `epd_gdep133c02`; application size **1071504 bytes**, fitting its configured partition. Flash binaries are not published in this source tree.
- Isolated loopback HTTP smoke: health and built frontend returned 200; image conversion returned 201; packed frame download returned 200, **960000 bytes**, with verified SHA-256 and `good-display-floyd-v1` processing. Only a synthetic solid-color image was used.
- Gitleaks 8.30.1 and exact production-secret-value matching on the source export found no leaks. Source membership excludes credentials, environments, caches, models, personal images, databases and compiled artifacts.

## Scope of evidence

The GitHub Actions workflow runs backend, frontend and host firmware checks; see its current result on the repository Actions page. Local ESP-IDF compilation is not an automated hardware test. No board was reflashed, no production service changed, no new GPU image generated, and no fresh panel ACK/visual acceptance was performed for publication. Historical device operation does not remove known model/time-of-day limitations. Vendor-derived command-sequence rights remain unresolved as documented in `THIRD_PARTY_NOTICES.md`.
