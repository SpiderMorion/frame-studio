from pathlib import Path

PRODUCTION_DEFAULTS = (
    Path(__file__).resolve().parents[1] / "firmware" / "esp-idf-production" / "sdkconfig.defaults"
)
PRODUCTION_MAIN = PRODUCTION_DEFAULTS.parent / "main" / "main.c"


def _read_kconfig_int(path: Path, key: str) -> int | None:
    prefix = f"{key}="
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return int(line.removeprefix(prefix))
    return None


def test_production_main_task_stack_covers_sd_http_runtime() -> None:
    stack_bytes = _read_kconfig_int(PRODUCTION_DEFAULTS, "CONFIG_ESP_MAIN_TASK_STACK_SIZE")

    assert stack_bytes is not None, "production main-task stack must be explicit"
    assert stack_bytes >= 12 * 1024


def test_production_firmware_reports_stack_fix_release() -> None:
    source = PRODUCTION_MAIN.read_text(encoding="utf-8")

    assert '#define FIRMWARE_VERSION "0.1.1"' in source
