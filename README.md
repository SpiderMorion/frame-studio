# Frame Studio

[![Source checks](https://github.com/SpiderMorion/frame-studio/actions/workflows/checks.yml/badge.svg)](https://github.com/SpiderMorion/frame-studio/actions/workflows/checks.yml)

Weather-driven image studio for one Good Display GDEP133C02 Spectra 6 display (ESP32-S3; logical 1600×1200 landscape, native 1200×1600). FastAPI/PostgreSQL backend, React UI, host-tested C++ core and ESP-IDF factory application. Source baseline c71b51f (V4 fixed 60-minute twilight; no Astral). Distribution of the included vendor-derived panel component is NOT covered by the root MIT license: read [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before redistribution. This repository does not ship model weights, photos, vendor archives or firmware binaries.

## What works and how

- Manual path, no GPU: upload an image to `POST /api/v1/conversions` or create a draft in the browser, adjust brightness, contrast, saturation, gamma, sharpness, dithering algorithm and strength, inspect nominal palette preview and publish. The backend converts to immutable original `source.png`, `preview.png`, packed `frame.bin` and manifest; browser does not quantize. History retains originals: reprocess via `POST /api/v1/conversions/{source_frame_id}` without overwriting them, and republish a saved frame via `POST /api/v1/frames/{frame_id}/publish`. Manual conversion and draft previews can run without DB; publishing/current device state and persisted history require PostgreSQL. See [color pipeline](docs/color-pipeline.md) and [backend API](docs/backend.md).
- Scheduled path: schedule interval/custom times and quiet hours in PostgreSQL; `eink-weather-scheduler` fetches Open-Meteo weather, builds prompt snapshots and durable generation jobs. `eink-spark-worker` leases jobs over token-authenticated private HTTP and submits a ComfyUI graph on the GPU host. `eink-home-render-worker` claims render jobs with DB/artifact access and publishes a verified frame. Default `qwen-image-2512`; graph alternatives `flux2-klein-4b`, `z-image-turbo` require their own models/nodes and are not promises of out-of-box compatibility. Optional existing stopped Docker ComfyUI container can be started on claim and stopped on completion/failure; without `EINK_COMFYUI_CONTAINER` worker expects always-on ComfyUI. [Model installation reference](deploy/README.md#gpu-host-and-models).
- Device polls `/api/v1/device/manifest` on private HTTP, verifies 960000-byte frame, stages it to microSD, refreshes dual-controller display, retains last-known-good and reports hardware status/ACK via device status endpoint. See [firmware build, wiring and provisioning](docs/firmware.md). Historical physical validation is not a current board test.

Data flow: weather → schedule/prompt → PostgreSQL jobs → GPU ComfyUI → immutable artifacts → current-frame manifest → ESP32-S3. Contracts are under `contracts/`; [architecture](docs/architecture.md), [deployment and backup](deploy/README.md), [Russian guide](README.ru.md).

## Manual quickstart (loopback; no GPU, no database)

Prerequisites: Python 3.12 with uv 0.12.21 and writable local directory; Node 22/npm only for browser UI. Execute from checkout root. `uv.lock`/`frontend/package-lock.json` pin dependencies; network access is needed on a cold install.

```sh
git clone https://github.com/SpiderMorion/frame-studio.git
cd frame-studio
uv sync --frozen --group dev
mkdir -p ./local-artifacts
EINK_ARTIFACT_ROOT="$PWD/local-artifacts" uv run --frozen uvicorn eink_frame.main:app --host 127.0.0.1 --port 8787
# in another terminal: upload your own image (no example photo is shipped)
curl -f -F 'source=@/absolute/path/to/image.png' -F 'processing_mode=ad-hoc' http://127.0.0.1:8787/api/v1/conversions
```

Ensure `DATABASE_URL` is **unset** in this shell. This returns a conversion manifest; DB-only endpoints return 503. For the browser build, run `(cd frontend && npm ci && npm run build)`, from the repository root, then restart the API with `EINK_FRONTEND_DIST="$PWD/frontend/dist"` alongside `EINK_ARTIFACT_ROOT`. Vite has no backend proxy; use the backend-served build for API workflows. For actual publish, history backed by DB, schedules and devices, follow [deployment](deploy/README.md) with an isolated PostgreSQL database. The source tree has no authentication for operator/UI APIs.

## Verify source locally

```sh
uv sync --frozen --group dev
env -u DATABASE_URL uv run --frozen pytest -q   # live PostgreSQL tests skipped
env -u DATABASE_URL uv run --frozen ruff check src tests migrations
env -u DATABASE_URL uv run --frozen mypy
env -u DATABASE_URL uv run --frozen python -c 'import eink_frame.main; import eink_frame.converter'
(cd frontend && npm ci --no-audit --no-fund && npm test && npm run lint && npm run build)
cmake -S firmware -B firmware/build && cmake --build firmware/build && ctest --test-dir firmware/build --output-on-failure
```

Additional host firmware test projects and ESP-IDF steps: [docs/firmware.md](docs/firmware.md). Tests without a dedicated disposable PostgreSQL do not validate DB migrations/integration or a real device.

## Limits / security

Single frame. V4 twilight fixed at 60 minutes, without Astral. No image-level night-scene semantic gate: generated night images can depict daylight. Default scene catalog is Taganrog-oriented; change `EINK_LOCATION_*` and `EINK_TIMEZONE`. Operator/UI routes have no user authentication; worker and device bearer tokens do **not** protect them. Bind to loopback/trusted LAN or VPN with independent access controls; never expose directly to the Internet. Device traffic is HTTP without TLS; provisioning SoftAP credentials are MAC-derived, not strong secrets. No signed OTA, deep sleep or DS18B20; factory partition layout has no OTA slots. Root MIT applies to independently owned project source only; it does **not** grant rights to vendor-derived panel files, model weights or dependencies (see [notices](THIRD_PARTY_NOTICES.md)). See the live CI badge above and [local verification record](docs/verification.md). No new live hardware test is claimed.
