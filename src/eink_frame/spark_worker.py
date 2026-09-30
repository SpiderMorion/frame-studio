from __future__ import annotations

import argparse
import math
import os
import subprocess
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field

ModelName = Literal["flux2-klein-4b", "z-image-turbo", "qwen-image-2512"]
ComfyGraph = dict[str, dict[str, Any]]


class ComfyUILifecycle(Protocol):
    def prepare(
        self, *, heartbeat: Callable[[], None], required_models: tuple[str, ...]
    ) -> None: ...

    def stop(self) -> None: ...


class AlwaysOnComfyUILifecycle:
    def prepare(
        self, *, heartbeat: Callable[[], None], required_models: tuple[str, ...]
    ) -> None:
        del heartbeat, required_models

    def stop(self) -> None:
        pass


class DockerComfyUILifecycle:
    def __init__(
        self,
        *,
        container: str,
        client: httpx.Client,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        start_timeout_seconds: float = 30,
        ready_timeout_seconds: float = 300,
        stop_timeout_seconds: float = 30,
        poll_interval_seconds: float = 2,
    ) -> None:
        self._container = container
        self._client = client
        self._runner = runner
        self._sleep = sleep
        self._monotonic = monotonic
        self._start_timeout_seconds = start_timeout_seconds
        self._ready_timeout_seconds = ready_timeout_seconds
        self._stop_timeout_seconds = stop_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds

    def initialize(self) -> None:
        self._run_stop(check=True)

    def prepare(
        self, *, heartbeat: Callable[[], None], required_models: tuple[str, ...]
    ) -> None:
        self._runner(
            ["docker", "start", self._container],
            check=True,
            timeout=self._start_timeout_seconds,
            capture_output=True,
            text=True,
        )
        deadline = self._monotonic() + self._ready_timeout_seconds
        while True:
            heartbeat()
            try:
                response = self._client.get("/system_stats")
                response.raise_for_status()
                break
            except httpx.HTTPError as error:
                if self._monotonic() >= deadline:
                    raise TimeoutError("ComfyUI readiness timed out") from error
                self._sleep(self._poll_interval_seconds)
        object_info = self._client.get("/object_info")
        object_info.raise_for_status()
        metadata = object_info.json()
        missing = [
            filename for filename in required_models if not _model_available(metadata, filename)
        ]
        if missing:
            raise RuntimeError(f"ComfyUI missing required models: {', '.join(missing)}")

    def stop(self) -> None:
        self._run_stop(check=True)

    def _run_stop(self, *, check: bool) -> None:
        self._runner(
            [
                "docker",
                "stop",
                "--time",
                str(int(self._stop_timeout_seconds)),
                self._container,
            ],
            check=check,
            timeout=self._stop_timeout_seconds + 5,
            capture_output=True,
            text=True,
        )


_MODEL_LOADER_INPUTS = {
    "qwen_2.5_vl_7b_fp8_scaled.safetensors": ("CLIPLoader", "clip_name"),
    "qwen_3_4b.safetensors": ("CLIPLoader", "clip_name"),
    "qwen_image_vae.safetensors": ("VAELoader", "vae_name"),
    "flux2-vae.safetensors": ("VAELoader", "vae_name"),
    "ae.safetensors": ("VAELoader", "vae_name"),
    "qwen_image_2512_fp8_e4m3fn.safetensors": ("UNETLoader", "unet_name"),
    "flux-2-klein-4b.safetensors": ("UNETLoader", "unet_name"),
    "z_image_turbo_bf16.safetensors": ("UNETLoader", "unet_name"),
}


def _model_available(metadata: object, filename: str) -> bool:
    loader = _MODEL_LOADER_INPUTS.get(filename)
    if loader is None:
        return False
    if not isinstance(metadata, dict):
        return False
    class_name, input_name = loader
    class_info = metadata.get(class_name)
    if not isinstance(class_info, dict):
        return False
    inputs = class_info.get("input")
    if not isinstance(inputs, dict):
        return False
    required = inputs.get("required")
    if not isinstance(required, dict):
        return False
    input_spec = required.get(input_name)
    if not isinstance(input_spec, list) or not input_spec:
        return False
    allowed_values = input_spec[0]
    return (
        isinstance(allowed_values, list)
        and all(isinstance(value, str) for value in allowed_values)
        and filename in allowed_values
    )


def _positive_finite_timeout(environ: Mapping[str, str], name: str, default: str) -> float:
    try:
        value = float(environ.get(name, default))
    except ValueError as error:
        raise ValueError(f"{name} must be a finite number greater than zero") from error
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite number greater than zero")
    return value


def build_comfy_lifecycle(
    *,
    client: httpx.Client,
    environ: Mapping[str, str] = os.environ,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ComfyUILifecycle:
    container = environ.get("EINK_COMFYUI_CONTAINER")
    if not container:
        return AlwaysOnComfyUILifecycle()
    lifecycle = DockerComfyUILifecycle(
        container=container,
        client=client,
        runner=runner,
        start_timeout_seconds=_positive_finite_timeout(
            environ, "EINK_COMFYUI_START_TIMEOUT", "30"
        ),
        ready_timeout_seconds=_positive_finite_timeout(
            environ, "EINK_COMFYUI_READY_TIMEOUT", "300"
        ),
        stop_timeout_seconds=_positive_finite_timeout(environ, "EINK_COMFYUI_STOP_TIMEOUT", "30"),
    )
    lifecycle.initialize()
    return lifecycle


class GenerationPayload(BaseModel):
    model: ModelName = "qwen-image-2512"
    prompt: str = Field(min_length=1, max_length=12000)
    negative_prompt: str = Field(
        default="text, lettering, logo, watermark, border, user interface, malformed composition",
        min_length=1,
        max_length=12000,
    )
    seed: int = Field(ge=0, lt=2**63)
    width: int = Field(ge=256, le=2048, multiple_of=16)
    height: int = Field(ge=256, le=2048, multiple_of=16)


def build_comfy_prompt(payload: GenerationPayload, *, filename_prefix: str) -> ComfyGraph:
    if payload.model == "flux2-klein-4b":
        return {
            "1": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": "flux-2-klein-4b.safetensors", "weight_dtype": "default"},
            },
            "2": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": "qwen_3_4b.safetensors",
                    "type": "flux2",
                    "device": "default",
                },
            },
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}},
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": payload.prompt, "clip": ["2", 0]},
            },
            "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
            "6": {
                "class_type": "EmptyFlux2LatentImage",
                "inputs": {"width": payload.width, "height": payload.height, "batch_size": 1},
            },
            "7": {"class_type": "RandomNoise", "inputs": {"noise_seed": payload.seed}},
            "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
            "9": {
                "class_type": "Flux2Scheduler",
                "inputs": {"steps": 4, "width": payload.width, "height": payload.height},
            },
            "10": {
                "class_type": "CFGGuider",
                "inputs": {
                    "model": ["1", 0],
                    "positive": ["4", 0],
                    "negative": ["5", 0],
                    "cfg": 1.0,
                },
            },
            "11": {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {
                    "noise": ["7", 0],
                    "guider": ["10", 0],
                    "sampler": ["8", 0],
                    "sigmas": ["9", 0],
                    "latent_image": ["6", 0],
                },
            },
            "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
            "13": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": filename_prefix, "images": ["12", 0]},
            },
        }
    if payload.model == "z-image-turbo":
        return {
            "1": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": "qwen_3_4b.safetensors",
                    "type": "lumina2",
                    "device": "default",
                },
            },
            "2": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
            "3": {
                "class_type": "UNETLoader",
                "inputs": {
                    "unet_name": "z_image_turbo_bf16.safetensors",
                    "weight_dtype": "default",
                },
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": payload.prompt, "clip": ["1", 0]},
            },
            "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
            "6": {
                "class_type": "EmptySD3LatentImage",
                "inputs": {"width": payload.width, "height": payload.height, "batch_size": 1},
            },
            "7": {
                "class_type": "ModelSamplingAuraFlow",
                "inputs": {"model": ["3", 0], "shift": 3.0},
            },
            "8": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["7", 0],
                    "seed": payload.seed,
                    "steps": 8,
                    "cfg": 1.0,
                    "sampler_name": "res_multistep",
                    "scheduler": "simple",
                    "positive": ["4", 0],
                    "negative": ["5", 0],
                    "latent_image": ["6", 0],
                    "denoise": 1.0,
                },
            },
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["2", 0]}},
            "10": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": filename_prefix, "images": ["9", 0]},
            },
        }
    return {
        "1": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                "type": "qwen_image",
                "device": "default",
            },
        },
        "2": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "3": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": "qwen_image_2512_fp8_e4m3fn.safetensors",
                "weight_dtype": "default",
            },
        },
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["3", 0], "shift": 3.1}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": payload.prompt, "clip": ["1", 0]}},
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": payload.negative_prompt, "clip": ["1", 0]},
        },
        "7": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": payload.width, "height": payload.height, "batch_size": 1},
        },
        "8": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["4", 0],
                "seed": payload.seed,
                "steps": 30,
                "cfg": 4.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["5", 0],
                "negative": ["6", 0],
                "latent_image": ["7", 0],
                "denoise": 1.0,
            },
        },
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["2", 0]}},
        "10": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": filename_prefix, "images": ["9", 0]},
        },
    }


def required_model_filenames(payload: GenerationPayload) -> tuple[str, ...]:
    graph = build_comfy_prompt(payload, filename_prefix="validation/output")
    filenames: list[str] = []
    for node in graph.values():
        if node["class_type"] not in {"CLIPLoader", "VAELoader", "UNETLoader"}:
            continue
        filenames.extend(
            value
            for key, value in node["inputs"].items()
            if key in {"clip_name", "vae_name", "unet_name"} and isinstance(value, str)
        )
    return tuple(filenames)


class ComfyUIClient:
    def __init__(
        self, *, client: httpx.Client, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        self._client = client
        self._sleep = sleep

    def generate(
        self,
        payload: GenerationPayload,
        *,
        filename_prefix: str,
        heartbeat: Callable[[], None],
        timeout_seconds: float = 1800,
    ) -> bytes:
        graph = build_comfy_prompt(payload, filename_prefix=filename_prefix)
        submitted = self._client.post(
            "/prompt",
            json={"prompt": graph, "client_id": str(uuid.uuid4())},
        )
        submitted.raise_for_status()
        prompt_id = submitted.json()["prompt_id"]
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            heartbeat()
            history_response = self._client.get(f"/history/{prompt_id}")
            history_response.raise_for_status()
            history = history_response.json()
            if prompt_id not in history:
                self._sleep(2)
                continue
            record = history[prompt_id]
            if record.get("status", {}).get("status_str") == "error":
                raise RuntimeError(f"ComfyUI prompt failed: {prompt_id}")
            images = [
                image
                for output in record.get("outputs", {}).values()
                for image in output.get("images", [])
            ]
            if not images:
                self._sleep(2)
                continue
            if len(images) != 1:
                raise RuntimeError(f"expected one ComfyUI image, received {len(images)}")
            image = images[0]
            downloaded = self._client.get(
                "/view",
                params={
                    "filename": image["filename"],
                    "subfolder": image.get("subfolder", ""),
                    "type": image.get("type", "output"),
                },
            )
            downloaded.raise_for_status()
            return downloaded.content
        raise TimeoutError(f"ComfyUI prompt timed out: {prompt_id}")


class CompletionUncertainError(RuntimeError):
    """Completion may have committed but could not be acknowledged safely."""


class BackendWorkerClient:
    def __init__(self, *, client: httpx.Client, token: str, worker_id: str) -> None:
        self._client = client
        self._headers = {"Authorization": f"Bearer {token}"}
        self.worker_id = worker_id

    def claim(self, *, lease_seconds: int = 600) -> dict[str, Any] | None:
        response = self._client.post(
            "/api/v1/workers/claim",
            json={
                "worker_id": self.worker_id,
                "lease_seconds": lease_seconds,
                "job_types": ["generate"],
            },
            headers=self._headers,
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        return dict(response.json())

    def heartbeat(self, job_id: str, *, lease_seconds: int = 600) -> None:
        response = self._client.post(
            f"/api/v1/jobs/{job_id}/heartbeat",
            json={"worker_id": self.worker_id, "lease_seconds": lease_seconds},
            headers=self._headers,
        )
        response.raise_for_status()

    def upload_output(self, job_id: str, content: bytes) -> dict[str, Any]:
        response = self._client.post(
            f"/api/v1/jobs/{job_id}/outputs",
            data={"worker_id": self.worker_id},
            files={"output": ("generated.png", content, "image/png")},
            headers=self._headers,
        )
        response.raise_for_status()
        return dict(response.json())

    def complete(self, job_id: str, *, result_refs: dict[str, Any]) -> None:
        last_error: httpx.HTTPError | None = None
        for _attempt in range(3):
            try:
                response = self._client.post(
                    f"/api/v1/jobs/{job_id}/complete",
                    json={"worker_id": self.worker_id, "result_refs": result_refs},
                    headers=self._headers,
                )
                response.raise_for_status()
                return
            except httpx.TransportError as error:
                last_error = error
                continue
            except httpx.HTTPStatusError as error:
                last_error = error
                break
        try:
            status_response = self._client.get(
                f"/api/v1/jobs/{job_id}", headers=self._headers
            )
            status_response.raise_for_status()
            status_body = status_response.json()
            if (
                status_body.get("status") == "succeeded"
                and status_body.get("result_refs") == result_refs
            ):
                return
        except (httpx.HTTPError, ValueError) as error:
            if isinstance(error, httpx.HTTPError):
                last_error = error
        raise CompletionUncertainError(
            f"completion acknowledgement could not be reconciled for job {job_id}"
        ) from last_error

    def fail(self, job_id: str, *, detail: str, retryable: bool = True) -> None:
        response = self._client.post(
            f"/api/v1/jobs/{job_id}/fail",
            json={
                "worker_id": self.worker_id,
                "error_code": "worker_error",
                "error_detail": detail[:2000],
                "retryable": retryable,
                "retry_delay_seconds": 30,
            },
            headers=self._headers,
        )
        response.raise_for_status()


class SparkWorker:
    def __init__(
        self,
        *,
        backend: BackendWorkerClient,
        generate: Callable[[GenerationPayload, Callable[[], None]], bytes],
        lifecycle: ComfyUILifecycle | None = None,
    ) -> None:
        self._backend = backend
        self._generate = generate
        self._lifecycle = lifecycle or AlwaysOnComfyUILifecycle()

    def run_once(self) -> bool:
        try:
            job = self._backend.claim()
        except httpx.HTTPError:
            return False
        if job is None:
            return False
        job_id = str(job["id"])
        lifecycle_started = False
        try:
            if job.get("job_type") != "generate":
                raise ValueError(f"unsupported Spark job type: {job.get('job_type')}")
            payload = GenerationPayload.model_validate(job["payload"])
            lifecycle_started = True
            self._lifecycle.prepare(
                heartbeat=lambda: self._backend.heartbeat(job_id),
                required_models=required_model_filenames(payload),
            )
            generated = self._generate(payload, lambda: self._backend.heartbeat(job_id))
            asset = self._backend.upload_output(job_id, generated)
        except (httpx.HTTPError, OSError, RuntimeError, TimeoutError, ValueError) as error:
            self._backend.fail(job_id, detail=f"{type(error).__name__}: {error}")
        else:
            try:
                self._backend.complete(job_id, result_refs={"provider_asset_id": asset["id"]})
            except CompletionUncertainError:
                pass
        finally:
            if lifecycle_started:
                self._lifecycle.stop()
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description="E-Ink DGX Spark generation worker")
    parser.add_argument("--once", action="store_true", help="claim at most one job and exit")
    args = parser.parse_args()
    backend_url = os.environ.get("EINK_BACKEND_URL", "http://127.0.0.1:8787")
    comfy_url = os.environ.get("EINK_COMFYUI_URL", "http://127.0.0.1:8188")
    worker_id = os.environ.get("EINK_WORKER_ID", "spark-worker")
    token = os.environ.get("EINK_FRAME_WORKER_TOKEN")
    if not token:
        raise SystemExit("EINK_FRAME_WORKER_TOKEN is required")
    with (
        httpx.Client(base_url=backend_url, timeout=60) as backend_http,
        httpx.Client(base_url=comfy_url, timeout=60) as comfy_http,
    ):
        backend = BackendWorkerClient(client=backend_http, token=token, worker_id=worker_id)
        comfy = ComfyUIClient(client=comfy_http)
        lifecycle = build_comfy_lifecycle(client=comfy_http)

        def generate(payload: GenerationPayload, heartbeat: Callable[[], None]) -> bytes:
            prefix = f"worker/{worker_id}/{uuid.uuid4()}/output"
            return comfy.generate(payload, filename_prefix=prefix, heartbeat=heartbeat)

        worker = SparkWorker(backend=backend, generate=generate, lifecycle=lifecycle)
        if args.once:
            worker.run_once()
            return
        while True:
            if not worker.run_once():
                time.sleep(5)
