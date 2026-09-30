# Deployment guide (operator-run example, NOT an installer)

These commands describe a **new isolated** Linux host, not a production change performed for this task. Review commands and substitute paths, users, addresses and secrets before running. Avoid a personal editable checkout; stage reviewed release source under `/opt/frame-studio` and private configuration under `/etc/frame-studio/runtime.env`. Requires Python 3.12, uv 0.12.21, Node 22/npm, PostgreSQL (historically 16), enough disk for immutable originals/frames, and a private LAN/VPN for devices. The app has no operator/UI authentication. Do not directly expose port 8787 to the Internet. This guide does not grant redistribution rights for the vendor-derived driver; see [notices](../THIRD_PARTY_NOTICES.md).

## Host, DB, application

As authorized administrator on a **new host**, create a dedicated service account, restricted runtime directory and artifacts (adapt distro-specific tools):

```sh
sudo useradd --system --home /var/lib/frame-studio --shell /usr/sbin/nologin frame-studio
sudo install -d -o frame-studio -g frame-studio -m 0700 /var/lib/frame-studio/artifacts
sudo install -d -o root -g root -m 0700 /etc/frame-studio
# stage reviewed source in /opt/frame-studio, readable by frame-studio; do not copy .env/credentials there
cd /opt/frame-studio
uv sync --frozen --no-dev
cd frontend && npm ci --no-audit --no-fund && npm run build && cd ..
```

Provision dedicated DB/role with PostgreSQL administration tooling (`sudo -u postgres psql` shown as an example); replace the sample password with a privately generated value, never put a real credential in shell history/source:

```sql
CREATE ROLE frame_studio LOGIN PASSWORD 'REPLACE_WITH_PRIVATE_SECRET';
CREATE DATABASE frame_studio OWNER frame_studio;
```

Create `/etc/frame-studio/runtime.env` privately, as root-owned systemd `EnvironmentFile` with mode 0600; NOT a shell script. Use [`.env.example`](../.env.example) as a **key list**, not a live credential file. Example values (all `REPLACE_*` placeholders MUST be changed):

```ini
DATABASE_URL=postgresql+psycopg://frame_studio:REPLACE_PASSWORD@127.0.0.1:5432/frame_studio
EINK_ARTIFACT_ROOT=/var/lib/frame-studio/artifacts
EINK_FRONTEND_DIST=/opt/frame-studio/frontend/dist
EINK_FRAME_WORKER_TOKEN=REPLACE_RANDOM_WORKER_TOKEN
EINK_FRAME_DEVICE_TOKEN=REPLACE_RANDOM_DEVICE_TOKEN
EINK_LOCATION_LATITUDE=REPLACE_LATITUDE
EINK_LOCATION_LONGITUDE=REPLACE_LONGITUDE
EINK_LOCATION_NAME=REPLACE_LOCATION
EINK_TIMEZONE=Etc/UTC
EINK_PANEL_ROTATION=transverse
```

Secrets must not enter logs, images or git. `EINK_PANEL_ROTATION=transverse` is the historically tested landscape mapping (code default differs). URL-encode special characters in DB passwords. `migrations/env.py` uses `DATABASE_URL` when `alembic.ini` retains its default. As the service user, with the **private environment loaded by an approved secret mechanism**, run `/opt/frame-studio/.venv/bin/alembic upgrade head` and `/opt/frame-studio/.venv/bin/alembic current`; do not use `Base.metadata.create_all` to deploy. Do not point `tests/test_postgresql_live.py` at this DB: it modifies/deletes tables. See [backend](../docs/backend.md) for the no-DB alternative.

After permissions, address and database migration are reviewed, `deploy/systemd/eink-weather-frame.service`, `eink-weather-scheduler.service` and `eink-home-render-worker.service` are **system unit templates**. Their API `ExecStartPre` runs migrations. Operator example, not executed here:

```sh
sudo install -m 0644 deploy/systemd/eink-weather-frame.service deploy/systemd/eink-weather-scheduler.service deploy/systemd/eink-home-render-worker.service /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/eink-weather-frame.service /etc/systemd/system/eink-weather-scheduler.service /etc/systemd/system/eink-home-render-worker.service
sudo systemctl daemon-reload
sudo systemctl enable --now eink-weather-frame.service
curl -f http://127.0.0.1:8787/api/v1/health
sudo systemctl enable --now eink-weather-scheduler.service eink-home-render-worker.service
```

Unit API binds loopback; devices/GPU workers need a reviewed private reachable endpoint and firewall/VPN, or an appropriately authenticated reverse proxy. Do not merely change `--host` to `0.0.0.0` on an untrusted network. Pause scheduler/job claims before code or schema rollback; older code is not guaranteed to read newer schema. Observe `journalctl -u eink-weather-frame.service -u eink-weather-scheduler.service -u eink-home-render-worker.service` and health/jobs/device state; health alone does not prove the pipeline.

## GPU host and models

Provision ComfyUI separately on a GPU host, install matching supported nodes, model files and required licenses from their publishers; no weights, container specification or auto-downloads ship here. Exact *expected filenames* from `src/eink_frame/spark_worker.py` (ComfyUI must expose them via loader object-info; conventional folders below depend on your ComfyUI version):

| Graph | `models/diffusion_models` (UNETLoader) | `models/text_encoders` (CLIPLoader) | `models/vae` (VAELoader) |
| --- | --- | --- | --- |
| default Qwen-Image-2512 | `qwen_image_2512_fp8_e4m3fn.safetensors` | `qwen_2.5_vl_7b_fp8_scaled.safetensors` | `qwen_image_vae.safetensors` |
| FLUX.2 Klein 4B | `flux-2-klein-4b.safetensors` | `qwen_3_4b.safetensors` | `flux2-vae.safetensors` |
| Z-Image Turbo | `z_image_turbo_bf16.safetensors` | `qwen_3_4b.safetensors` | `ae.safetensors` |

Choose approved model artifacts from publisher pages, validate checksums/license yourself and confirm model/node names in ComfyUI `GET /object_info` before accepting jobs. Exact local paths may differ (ComfyUI's `extra_model_paths.yaml` can remap); filenames and loader types above come from code, not an installation guarantee. Required graph nodes include `EmptyFlux2LatentImage`/`Flux2Scheduler` for FLUX.2 and `ModelSamplingAuraFlow` for Qwen/Z; verify compatibility with your installed ComfyUI version. GPU sizing/performance and image semantics are not certified.

Build the package from the reviewed repository root with `uv build --wheel`. Transfer `dist/eink_weather_frame-0.1.0-py3-none-any.whl` to the GPU host, then create a clean environment with `python3.12 -m venv "$HOME/frame-studio-venv"` and install with `"$HOME/frame-studio-venv/bin/pip" install /path/to/eink_weather_frame-0.1.0-py3-none-any.whl`.

Install the reviewed `eink-weather-frame` wheel in an isolated `%h/frame-studio-venv` on the GPU machine; don't rely on overwriting an existing identically versioned `0.1.0` wheel (use a clean venv/force reinstall). Configure private `%h/.config/frame-studio/worker.env` with `EINK_BACKEND_URL=http://PRIVATE_HOST:8787`, shared `EINK_FRAME_WORKER_TOKEN`, unique `EINK_WORKER_ID`, `EINK_COMFYUI_URL=http://127.0.0.1:8188`. For always-on ComfyUI run `%h/frame-studio-venv/bin/eink-spark-worker` with that environment. The supplied `deploy/systemd/eink-weather-worker.service` is a **user unit for container mode only**: set `EINK_COMFYUI_CONTAINER` to an existing stopped container with restart policy `no`, grant controlled Docker access and set positive `EINK_COMFYUI_STOP_TIMEOUT`; the worker starts on claim, stops after completion/failure, and unit `ExecStopPost` attempts cleanup. For always-on mode remove the template's container-specific `ExecStopPost` first. Do not assume Docker groups are a safe isolation boundary. No external worker/service was started for this documentation task.

## Backups, recovery and failures

Pause writes/scheduler for a consistent point-in-time copy; as authorized database role: `pg_dump -Fc -f /path/to/private-backup/frame_studio.dump frame_studio`. Back up `/var/lib/frame-studio/artifacts` **at the corresponding point** and private configuration separately, encrypted and permissions-preserving. DB-only dump cannot reconstruct original images/packed frames. Restore into a NEW isolated DB/artifact root, inspect `alembic current`, verify manifest/frame hash/download and only then cut over. Never overwrite immutable artifacts in place. For failed generation inspect job state, scheduler/GPU/home-render logs, `/object_info` loader names, network connectivity and free space. For device refresh failure preserve SD last-known-good, check SPI/BUSY/power, manifest hash and `/api/v1/device/status`; SW4 at boot resets configuration/manifest state and requires physically controlled reprovisioning. Firmware and flash safety: [docs/firmware.md](../docs/firmware.md).
