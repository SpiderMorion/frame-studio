from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest

from eink_frame.spark_worker import (
    BackendWorkerClient,
    ComfyUIClient,
    CompletionUncertainError,
    DockerComfyUILifecycle,
    GenerationPayload,
    SparkWorker,
    _model_available,
    build_comfy_lifecycle,
    build_comfy_prompt,
)


def test_generation_payload_defaults_to_qwen_image_2512() -> None:
    payload = GenerationPayload(
        prompt="A full-bleed photorealistic weather scene",
        seed=12345,
        width=1600,
        height=1200,
    )

    assert payload.model == "qwen-image-2512"


@pytest.mark.parametrize(
    ("model", "expected_steps"),
    [
        ("flux2-klein-4b", 4),
        ("z-image-turbo", 8),
        ("qwen-image-2512", 30),
    ],
)
def test_comfy_graph_uses_requested_geometry_seed_and_model(
    model: str, expected_steps: int
) -> None:
    payload = GenerationPayload(
        model=model,
        prompt="A full-bleed photorealistic weather scene",
        seed=12345,
        width=1600,
        height=1200,
    )

    graph = build_comfy_prompt(payload, filename_prefix="jobs/job-1/output")

    assert any(node["class_type"] == "SaveImage" for node in graph.values())
    assert any(
        node["inputs"].get("width") == 1600 and node["inputs"].get("height") == 1200
        for node in graph.values()
    )
    sampler_steps = [
        node["inputs"].get("steps")
        for node in graph.values()
        if node["class_type"] in {"KSampler", "Flux2Scheduler"}
    ]
    assert expected_steps in sampler_steps
    serialized = repr(graph)
    assert str(payload.seed) in serialized
    assert payload.prompt in serialized


def test_qwen_comfy_graph_uses_requested_negative_prompt() -> None:
    payload = GenerationPayload(
        model="qwen-image-2512",
        prompt="An enclosed lively city courtyard",
        negative_prompt="sea, harbor, port, ship, distant water horizon",
        seed=12345,
        width=1600,
        height=1200,
    )

    graph = build_comfy_prompt(payload, filename_prefix="jobs/job-1/output")

    assert graph["6"]["inputs"]["text"] == payload.negative_prompt


def test_comfy_client_submits_polls_heartbeats_and_downloads_output() -> None:
    history_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal history_calls
        if request.method == "POST" and request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "prompt-1"})
        if request.url.path == "/history/prompt-1":
            history_calls += 1
            if history_calls == 1:
                return httpx.Response(200, json={})
            return httpx.Response(
                200,
                json={
                    "prompt-1": {
                        "status": {"status_str": "success"},
                        "outputs": {
                            "10": {
                                "images": [
                                    {
                                        "filename": "output.png",
                                        "subfolder": "jobs/job-1",
                                        "type": "output",
                                    }
                                ]
                            }
                        },
                    }
                },
            )
        if request.url.path == "/view":
            assert request.url.params["filename"] == "output.png"
            return httpx.Response(200, content=b"PNG output")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    heartbeats: list[int] = []
    with httpx.Client(transport=httpx.MockTransport(handler), base_url="http://comfy") as client:
        comfy = ComfyUIClient(client=client, sleep=lambda _: None)
        result = comfy.generate(
            GenerationPayload(
                model="z-image-turbo",
                prompt="weather",
                seed=7,
                width=1600,
                height=1200,
            ),
            filename_prefix="jobs/job-1/output",
            heartbeat=lambda: heartbeats.append(1),
        )

    assert result == b"PNG output"
    assert history_calls == 2
    assert heartbeats


def test_spark_worker_claims_generates_uploads_and_completes() -> None:
    calls: list[str] = []

    def backend_handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/v1/workers/claim":
            return httpx.Response(
                200,
                json={
                    "id": "7f3a2cb2-a908-4216-84e4-7987127e7acc",
                    "job_type": "generate",
                    "payload": {
                        "model": "flux2-klein-4b",
                        "prompt": "weather",
                        "seed": 12,
                        "width": 1600,
                        "height": 1200,
                    },
                },
            )
        if request.url.path.endswith("/heartbeat"):
            return httpx.Response(204)
        if request.url.path.endswith("/outputs"):
            return httpx.Response(201, json={"id": "asset-1"})
        if request.url.path.endswith("/complete"):
            return httpx.Response(200, json={"status": "succeeded"})
        raise AssertionError(f"unexpected backend request: {request.url}")

    generated: list[GenerationPayload] = []

    def generate(payload: GenerationPayload, heartbeat: object) -> bytes:
        generated.append(payload)
        assert callable(heartbeat)
        heartbeat()
        return b"generated png"

    with httpx.Client(
        transport=httpx.MockTransport(backend_handler), base_url="http://backend"
    ) as client:
        worker = SparkWorker(
            backend=BackendWorkerClient(client=client, token="token", worker_id="spark-a"),
            generate=generate,
        )
        worked = worker.run_once()

    assert worked is True
    assert generated[0].model == "flux2-klein-4b"
    assert calls == [
        "/api/v1/workers/claim",
        "/api/v1/jobs/7f3a2cb2-a908-4216-84e4-7987127e7acc/heartbeat",
        "/api/v1/jobs/7f3a2cb2-a908-4216-84e4-7987127e7acc/outputs",
        "/api/v1/jobs/7f3a2cb2-a908-4216-84e4-7987127e7acc/complete",
    ]


def test_complete_reconciles_when_response_is_lost_after_commit() -> None:
    complete_attempts = 0
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal complete_attempts
        calls.append(request.url.path)
        if request.url.path.endswith("/complete"):
            complete_attempts += 1
            raise httpx.ReadTimeout("response lost", request=request)
        if request.method == "GET" and request.url.path.endswith("/jobs/job-1"):
            return httpx.Response(
                200,
                json={"status": "succeeded", "result_refs": {"provider_asset_id": "asset-1"}},
            )
        raise AssertionError(f"unexpected request: {request.url}")

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://backend"
    ) as client:
        backend = BackendWorkerClient(client=client, token="token", worker_id="spark-a")
        backend.complete("job-1", result_refs={"provider_asset_id": "asset-1"})

    assert complete_attempts == 3
    assert calls[-1] == "/api/v1/jobs/job-1"


def test_unresolved_complete_never_fails_possibly_succeeded_job() -> None:
    calls: list[str] = []

    class Backend:
        def claim(self) -> dict[str, object]:
            return {
                "id": "job-id",
                "job_type": "generate",
                "payload": {
                    "model": "qwen-image-2512",
                    "prompt": "weather",
                    "seed": 1,
                    "width": 1600,
                    "height": 1200,
                },
            }

        def heartbeat(self, job_id: str) -> None:
            calls.append("heartbeat")

        def upload_output(self, job_id: str, content: bytes) -> dict[str, str]:
            return {"id": "asset-id"}

        def complete(self, job_id: str, *, result_refs: dict[str, object]) -> None:
            calls.append("complete")
            raise CompletionUncertainError("could not reconcile completion")

        def fail(self, job_id: str, *, detail: str, retryable: bool = True) -> None:
            calls.append("fail")

    class Lifecycle:
        def prepare(self, *, heartbeat: object, required_models: tuple[str, ...]) -> None:
            calls.append("prepare")

        def stop(self) -> None:
            calls.append("stop")

    worker = SparkWorker(  # type: ignore[arg-type]
        backend=Backend(), generate=lambda payload, heartbeat: b"image", lifecycle=Lifecycle()
    )

    assert worker.run_once() is True
    assert "fail" not in calls
    assert calls[-1] == "stop"


def test_spark_worker_tolerates_backend_unavailable_before_claim() -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("backend unavailable", request=request)

    generated = False

    def generate(payload: GenerationPayload, heartbeat: object) -> bytes:
        nonlocal generated
        generated = True
        return b"unused"

    with httpx.Client(
        transport=httpx.MockTransport(unavailable), base_url="http://backend"
    ) as client:
        worker = SparkWorker(
            backend=BackendWorkerClient(client=client, token="token", worker_id="spark-a"),
            generate=generate,
        )

        assert worker.run_once() is False

    assert generated is False


def test_spark_worker_does_not_start_comfyui_without_claimed_job() -> None:
    lifecycle_calls: list[str] = []

    class RecordingLifecycle:
        def prepare(self, *, heartbeat: object, required_models: tuple[str, ...]) -> None:
            lifecycle_calls.append("prepare")

        def stop(self) -> None:
            lifecycle_calls.append("stop")

    def no_job(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/workers/claim"
        return httpx.Response(204)

    with httpx.Client(
        transport=httpx.MockTransport(no_job), base_url="http://backend"
    ) as client:
        worker = SparkWorker(
            backend=BackendWorkerClient(client=client, token="token", worker_id="spark-a"),
            generate=lambda payload, heartbeat: b"unused",
            lifecycle=RecordingLifecycle(),
        )
        assert worker.run_once() is False

    assert lifecycle_calls == []


def test_spark_worker_prepares_heartbeats_and_stops_comfyui_on_success() -> None:
    backend_calls: list[str] = []
    lifecycle_calls: list[object] = []
    job_id = "7f3a2cb2-a908-4216-84e4-7987127e7acc"

    class RecordingLifecycle:
        def prepare(
            self, *, heartbeat: object, required_models: tuple[str, ...]
        ) -> None:
            lifecycle_calls.append(("prepare", required_models))
            assert callable(heartbeat)
            heartbeat()

        def stop(self) -> None:
            lifecycle_calls.append("stop")

    def backend_handler(request: httpx.Request) -> httpx.Response:
        backend_calls.append(request.url.path)
        if request.url.path == "/api/v1/workers/claim":
            return httpx.Response(
                200,
                json={
                    "id": job_id,
                    "job_type": "generate",
                    "payload": {
                        "model": "qwen-image-2512",
                        "prompt": "weather",
                        "seed": 12,
                        "width": 1600,
                        "height": 1200,
                    },
                },
            )
        if request.url.path.endswith("/heartbeat"):
            return httpx.Response(204)
        if request.url.path.endswith("/outputs"):
            return httpx.Response(201, json={"id": "asset-1"})
        if request.url.path.endswith("/complete"):
            return httpx.Response(200)
        raise AssertionError(f"unexpected backend request: {request.url}")

    with httpx.Client(
        transport=httpx.MockTransport(backend_handler), base_url="http://backend"
    ) as client:
        worker = SparkWorker(
            backend=BackendWorkerClient(client=client, token="token", worker_id="spark-a"),
            generate=lambda payload, heartbeat: b"generated",
            lifecycle=RecordingLifecycle(),
        )
        assert worker.run_once() is True

    assert lifecycle_calls == [
        (
            "prepare",
            (
                "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                "qwen_image_vae.safetensors",
                "qwen_image_2512_fp8_e4m3fn.safetensors",
            ),
        ),
        "stop",
    ]
    assert f"/api/v1/jobs/{job_id}/heartbeat" in backend_calls


def test_spark_worker_fails_job_and_stops_comfyui_on_generation_failure() -> None:
    backend_calls: list[str] = []
    lifecycle_calls: list[str] = []

    class RecordingLifecycle:
        def prepare(self, *, heartbeat: object, required_models: tuple[str, ...]) -> None:
            lifecycle_calls.append("prepare")

        def stop(self) -> None:
            lifecycle_calls.append("stop")

    def backend_handler(request: httpx.Request) -> httpx.Response:
        backend_calls.append(request.url.path)
        if request.url.path == "/api/v1/workers/claim":
            return httpx.Response(
                200,
                json={
                    "id": "7f3a2cb2-a908-4216-84e4-7987127e7acc",
                    "job_type": "generate",
                    "payload": {
                        "model": "qwen-image-2512",
                        "prompt": "weather",
                        "seed": 12,
                        "width": 1600,
                        "height": 1200,
                    },
                },
            )
        if request.url.path.endswith("/fail"):
            return httpx.Response(200)
        raise AssertionError(f"unexpected backend request: {request.url}")

    def fail_generation(payload: GenerationPayload, heartbeat: object) -> bytes:
        raise OSError("GPU read failed")

    with httpx.Client(
        transport=httpx.MockTransport(backend_handler), base_url="http://backend"
    ) as client:
        worker = SparkWorker(
            backend=BackendWorkerClient(client=client, token="token", worker_id="spark-a"),
            generate=fail_generation,
            lifecycle=RecordingLifecycle(),
        )
        assert worker.run_once() is True

    assert lifecycle_calls == ["prepare", "stop"]
    assert backend_calls[-1].endswith("/fail")


def test_stop_failure_after_complete_terminates_without_failing_completed_job() -> None:
    backend_calls: list[str] = []

    class StopFailsLifecycle:
        def prepare(self, *, heartbeat: object, required_models: tuple[str, ...]) -> None:
            pass

        def stop(self) -> None:
            raise subprocess.CalledProcessError(1, ["docker", "stop", "eink-comfy"])

    def backend_handler(request: httpx.Request) -> httpx.Response:
        backend_calls.append(request.url.path)
        if request.url.path == "/api/v1/workers/claim":
            return httpx.Response(
                200,
                json={
                    "id": "7f3a2cb2-a908-4216-84e4-7987127e7acc",
                    "job_type": "generate",
                    "payload": {
                        "model": "qwen-image-2512",
                        "prompt": "weather",
                        "seed": 12,
                        "width": 1600,
                        "height": 1200,
                    },
                },
            )
        if request.url.path.endswith("/outputs"):
            return httpx.Response(201, json={"id": "asset-1"})
        if request.url.path.endswith("/complete"):
            return httpx.Response(200)
        raise AssertionError(f"unexpected backend request: {request.url}")

    with httpx.Client(
        transport=httpx.MockTransport(backend_handler), base_url="http://backend"
    ) as client:
        worker = SparkWorker(
            backend=BackendWorkerClient(client=client, token="token", worker_id="spark-a"),
            generate=lambda payload, heartbeat: b"generated",
            lifecycle=StopFailsLifecycle(),
        )
        with pytest.raises(subprocess.CalledProcessError):
            worker.run_once()

    assert any(path.endswith("/complete") for path in backend_calls)
    assert not any(path.endswith("/fail") for path in backend_calls)


def test_docker_lifecycle_uses_argv_and_validates_ready_models() -> None:
    commands: list[tuple[list[str], dict[str, object]]] = []

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)

    def comfy_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={"system": {"os": "linux"}})
        if request.url.path == "/object_info":
            return httpx.Response(
                200,
                json={
                    "CLIPLoader": {
                        "input": {
                            "required": {
                                "clip_name": [["qwen_2.5_vl_7b_fp8_scaled.safetensors"]]
                            }
                        }
                    },
                    "VAELoader": {
                        "input": {
                            "required": {"vae_name": [["qwen_image_vae.safetensors"]]}
                        }
                    },
                    "UNETLoader": {
                        "input": {
                            "required": {
                                "unet_name": [["qwen_image_2512_fp8_e4m3fn.safetensors"]]
                            }
                        }
                    },
                },
            )
        raise AssertionError(f"unexpected ComfyUI request: {request.url}")

    with httpx.Client(
        transport=httpx.MockTransport(comfy_handler), base_url="http://comfy"
    ) as client:
        lifecycle = DockerComfyUILifecycle(
            container="eink-comfy",
            client=client,
            runner=runner,
            sleep=lambda _: None,
            start_timeout_seconds=11,
            ready_timeout_seconds=22,
            stop_timeout_seconds=33,
        )
        lifecycle.initialize()
        lifecycle.prepare(
            heartbeat=lambda: None,
            required_models=(
                "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                "qwen_image_vae.safetensors",
                "qwen_image_2512_fp8_e4m3fn.safetensors",
            ),
        )
        lifecycle.stop()

    assert [command for command, _ in commands] == [
        ["docker", "stop", "--time", "33", "eink-comfy"],
        ["docker", "start", "eink-comfy"],
        ["docker", "stop", "--time", "33", "eink-comfy"],
    ]
    assert all(isinstance(command, list) for command, _ in commands)
    assert all(kwargs.get("shell") is not True for _, kwargs in commands)
    assert commands[0][1]["check"] is True
    assert commands[1][1]["timeout"] == 11


def test_model_validation_failure_fails_job_and_stops_container() -> None:
    backend_calls: list[str] = []
    docker_commands: list[list[str]] = []
    generated = False

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        docker_commands.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    def comfy_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={})
        if request.url.path == "/object_info":
            return httpx.Response(
                200,
                json={
                    "CLIPLoader": {
                        "input": {
                            "required": {
                                "clip_name": [
                                    [
                                        "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                                        "qwen_image_2512_fp8_e4m3fn.safetensors",
                                        "qwen_image_vae.safetensors",
                                    ]
                                ]
                            }
                        }
                    }
                },
            )
        raise AssertionError(f"unexpected ComfyUI request: {request.url}")

    def backend_handler(request: httpx.Request) -> httpx.Response:
        backend_calls.append(request.url.path)
        if request.url.path == "/api/v1/workers/claim":
            return httpx.Response(
                200,
                json={
                    "id": "7f3a2cb2-a908-4216-84e4-7987127e7acc",
                    "job_type": "generate",
                    "payload": {
                        "model": "qwen-image-2512",
                        "prompt": "weather",
                        "seed": 12,
                        "width": 1600,
                        "height": 1200,
                    },
                },
            )
        if request.url.path.endswith("/heartbeat"):
            return httpx.Response(204)
        if request.url.path.endswith("/fail"):
            return httpx.Response(200)
        raise AssertionError(f"unexpected backend request: {request.url}")

    def generate(payload: GenerationPayload, heartbeat: object) -> bytes:
        nonlocal generated
        generated = True
        return b"unused"

    with (
        httpx.Client(
            transport=httpx.MockTransport(backend_handler), base_url="http://backend"
        ) as backend_http,
        httpx.Client(
            transport=httpx.MockTransport(comfy_handler), base_url="http://comfy"
        ) as comfy_http,
    ):
        worker = SparkWorker(
            backend=BackendWorkerClient(
                client=backend_http, token="token", worker_id="spark-a"
            ),
            generate=generate,
            lifecycle=DockerComfyUILifecycle(
                container="eink-comfy", client=comfy_http, runner=runner
            ),
        )
        assert worker.run_once() is True

    assert generated is False
    assert backend_calls[-1].endswith("/fail")
    assert docker_commands[-1] == ["docker", "stop", "--time", "30", "eink-comfy"]


def test_readiness_timeout_fails_job_heartbeats_and_stops_container() -> None:
    backend_calls: list[str] = []
    docker_commands: list[list[str]] = []
    clock = iter([0.0, 0.5, 1.0])

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        docker_commands.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    def comfy_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/system_stats"
        return httpx.Response(503, json={"status": "starting"})

    def backend_handler(request: httpx.Request) -> httpx.Response:
        backend_calls.append(request.url.path)
        if request.url.path == "/api/v1/workers/claim":
            return httpx.Response(
                200,
                json={
                    "id": "7f3a2cb2-a908-4216-84e4-7987127e7acc",
                    "job_type": "generate",
                    "payload": {
                        "model": "qwen-image-2512",
                        "prompt": "weather",
                        "seed": 12,
                        "width": 1600,
                        "height": 1200,
                    },
                },
            )
        if request.url.path.endswith("/heartbeat"):
            return httpx.Response(204)
        if request.url.path.endswith("/fail"):
            return httpx.Response(200)
        raise AssertionError(f"unexpected backend request: {request.url}")

    with (
        httpx.Client(
            transport=httpx.MockTransport(backend_handler), base_url="http://backend"
        ) as backend_http,
        httpx.Client(
            transport=httpx.MockTransport(comfy_handler), base_url="http://comfy"
        ) as comfy_http,
    ):
        worker = SparkWorker(
            backend=BackendWorkerClient(
                client=backend_http, token="token", worker_id="spark-a"
            ),
            generate=lambda payload, heartbeat: b"unused",
            lifecycle=DockerComfyUILifecycle(
                container="eink-comfy",
                client=comfy_http,
                runner=runner,
                sleep=lambda _: None,
                monotonic=lambda: next(clock),
                ready_timeout_seconds=1,
            ),
        )
        assert worker.run_once() is True

    assert sum(path.endswith("/heartbeat") for path in backend_calls) == 2
    assert backend_calls[-1].endswith("/fail")
    assert docker_commands == [
        ["docker", "start", "eink-comfy"],
        ["docker", "stop", "--time", "30", "eink-comfy"],
    ]


def test_lifecycle_configuration_preserves_legacy_and_stops_enabled_container_at_startup() -> None:
    commands: list[tuple[list[str], dict[str, object]]] = []

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)

    with httpx.Client(base_url="http://comfy") as client:
        legacy = build_comfy_lifecycle(client=client, environ={}, runner=runner)
        enabled = build_comfy_lifecycle(
            client=client,
            environ={
                "EINK_COMFYUI_CONTAINER": "eink-comfy",
                "EINK_COMFYUI_START_TIMEOUT": "12",
                "EINK_COMFYUI_READY_TIMEOUT": "240",
                "EINK_COMFYUI_STOP_TIMEOUT": "34",
            },
            runner=runner,
        )

    assert legacy.__class__.__name__ == "AlwaysOnComfyUILifecycle"
    assert enabled.__class__.__name__ == "DockerComfyUILifecycle"
    assert [command for command, _ in commands] == [
        ["docker", "stop", "--time", "34", "eink-comfy"]
    ]
    assert commands[0][1]["check"] is True


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("EINK_COMFYUI_START_TIMEOUT", "0"),
        ("EINK_COMFYUI_READY_TIMEOUT", "-1"),
        ("EINK_COMFYUI_STOP_TIMEOUT", "nan"),
        ("EINK_COMFYUI_STOP_TIMEOUT", "inf"),
    ],
)
def test_lifecycle_rejects_non_positive_or_non_finite_timeouts(name: str, value: str) -> None:
    with httpx.Client(base_url="http://comfy") as client, pytest.raises(
        ValueError, match=f"{name} must be a finite number greater than zero"
    ):
        build_comfy_lifecycle(
            client=client,
            environ={"EINK_COMFYUI_CONTAINER": "eink-comfy", name: value},
        )


def test_model_validation_ignores_default_metadata_outside_allowed_values() -> None:
    metadata = {
        "UNETLoader": {
            "input": {
                "required": {
                    "unet_name": [
                        ["some-other-model.safetensors"],
                        {"default": "qwen_image_2512_fp8_e4m3fn.safetensors"},
                    ]
                }
            }
        }
    }

    assert not _model_available(metadata, "qwen_image_2512_fp8_e4m3fn.safetensors")


@pytest.mark.parametrize("backend_complete", [True, False])
def test_stop_failure_terminates_worker_without_failing_completed_job(
    backend_complete: bool,
) -> None:
    calls: list[str] = []

    class Backend:
        def claim(self) -> dict[str, object]:
            return {
                "id": "job-id",
                "job_type": "generate",
                "payload": {
                    "model": "qwen-image-2512",
                    "prompt": "weather",
                    "seed": 1,
                    "width": 1600,
                    "height": 1200,
                },
            }

        def heartbeat(self, job_id: str) -> None:
            calls.append("heartbeat")

        def upload_output(self, job_id: str, content: bytes) -> dict[str, str]:
            if not backend_complete:
                raise RuntimeError("generation failed")
            return {"id": "asset-id"}

        def complete(self, job_id: str, *, result_refs: dict[str, object]) -> None:
            calls.append("complete")

        def fail(self, job_id: str, *, detail: str, retryable: bool = True) -> None:
            calls.append("fail")

    class Lifecycle:
        def prepare(self, *, heartbeat: object, required_models: tuple[str, ...]) -> None:
            calls.append("prepare")

        def stop(self) -> None:
            calls.append("stop")
            raise subprocess.SubprocessError("docker stop failed")

    worker = SparkWorker(  # type: ignore[arg-type]
        backend=Backend(), generate=lambda payload, heartbeat: b"image", lifecycle=Lifecycle()
    )

    with pytest.raises(subprocess.SubprocessError, match="docker stop failed"):
        worker.run_once()

    assert calls.count("fail") == (0 if backend_complete else 1)
    if backend_complete:
        assert calls.index("complete") < calls.index("stop")


def test_systemd_unit_has_post_stop_container_cleanup() -> None:
    unit = Path("deploy/systemd/eink-weather-worker.service").read_text(encoding="utf-8")
    assert (
        "ExecStopPost=/usr/bin/docker stop --time ${EINK_COMFYUI_STOP_TIMEOUT} "
        "${EINK_COMFYUI_CONTAINER}"
    ) in unit
    assert "TimeoutStopSec=75s" in unit
    assert "ExecStopPost=-" not in unit


def test_fail_endpoint_error_still_runs_cleanup_and_terminates_worker() -> None:
    calls: list[str] = []

    class Backend:
        def claim(self) -> dict[str, object]:
            return {
                "id": "job-id",
                "job_type": "generate",
                "payload": {
                    "model": "qwen-image-2512",
                    "prompt": "weather",
                    "seed": 1,
                    "width": 1600,
                    "height": 1200,
                },
            }

        def heartbeat(self, job_id: str) -> None:
            calls.append("heartbeat")

        def upload_output(self, job_id: str, content: bytes) -> dict[str, str]:
            raise RuntimeError("generation failed")

        def complete(self, job_id: str, *, result_refs: dict[str, object]) -> None:
            calls.append("complete")

        def fail(self, job_id: str, *, detail: str, retryable: bool = True) -> None:
            calls.append("fail")
            raise RuntimeError("fail endpoint unavailable")

    class Lifecycle:
        def prepare(self, *, heartbeat: object, required_models: tuple[str, ...]) -> None:
            calls.append("prepare")

        def stop(self) -> None:
            calls.append("stop")

    worker = SparkWorker(  # type: ignore[arg-type]
        backend=Backend(), generate=lambda payload, heartbeat: b"image", lifecycle=Lifecycle()
    )

    with pytest.raises(RuntimeError, match="fail endpoint unavailable"):
        worker.run_once()

    assert calls[-2:] == ["fail", "stop"]


def test_second_job_starts_and_stops_container_again() -> None:
    claims = 0
    lifecycle_calls: list[str] = []

    class RecordingLifecycle:
        def prepare(self, *, heartbeat: object, required_models: tuple[str, ...]) -> None:
            lifecycle_calls.append("start")

        def stop(self) -> None:
            lifecycle_calls.append("stop")

    def backend_handler(request: httpx.Request) -> httpx.Response:
        nonlocal claims
        if request.url.path == "/api/v1/workers/claim":
            claims += 1
            return httpx.Response(
                200,
                json={
                    "id": f"7f3a2cb2-a908-4216-84e4-7987127e7ac{claims}",
                    "job_type": "generate",
                    "payload": {
                        "model": "qwen-image-2512",
                        "prompt": "weather",
                        "seed": claims,
                        "width": 1600,
                        "height": 1200,
                    },
                },
            )
        if request.url.path.endswith("/outputs"):
            return httpx.Response(201, json={"id": f"asset-{claims}"})
        if request.url.path.endswith("/complete"):
            return httpx.Response(200)
        raise AssertionError(f"unexpected backend request: {request.url}")

    with httpx.Client(
        transport=httpx.MockTransport(backend_handler), base_url="http://backend"
    ) as client:
        worker = SparkWorker(
            backend=BackendWorkerClient(client=client, token="token", worker_id="spark-a"),
            generate=lambda payload, heartbeat: b"generated",
            lifecycle=RecordingLifecycle(),
        )
        assert worker.run_once() is True
        assert worker.run_once() is True

    assert lifecycle_calls == ["start", "stop", "start", "stop"]
