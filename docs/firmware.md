# Firmware: ESP32-S3 GDEP133C02

`firmware/` is a C++20 host-test core for manifests, frame contract, CRC journal and state transitions. `firmware/esp-idf/` is an early prototype; `firmware/esp-idf-production/` is the factory ESP32-S3 application using shared `../esp-idf/components` (see `CMakeLists.txt`). The included `epd_gdep133c02` driver has vendor-derived command sequences with unresolved redistribution rights; [notices](../THIRD_PARTY_NOTICES.md). This source includes the driver but **no ESP-IDF toolchain or prebuilt firmware**. Historical production v0.1.1/physical diagnostic validated native 1200×1600 and transverse 1600×1200 landscape mapping; that is not a new build or board-revision certification.

## Connections and frame format

| Signal | ESP32-S3 GPIO | Source |
| --- | --- | --- |
| EPD CS0 / CS1 | 18 / 17 | `firmware/esp-idf/main/main.c`, `components/epd_gdep133c02/epd_gdep133c02.c` |
| EPD CLK / MOSI / MISO | 9 / 41 / 40 | same |
| EPD BUSY_N / RESET_N / LOAD_SW | 7 / 6 / 45 | same |
| SD CS / MOSI / MISO / CLK | 15 / 3 / 5 / 8 | `components/frame_store/frame_store.c` |
| SW4 at boot (reset provisioning/accepted state) | 14 | production application |

Check voltage, board schematic and harness before powering. Dual-controller 10 MHz SPI mode 0 panel; each native row uses 300 bytes per controller. Six colors use native nibble codes `(0,1,2,3,5,6)` for black/white/yellow/red/blue/green; 600 bytes per native row × 1600 rows = 960000 bytes. Vendor packing reverses packed byte pairs within each row, not color order. Set `EINK_PANEL_ROTATION=transverse` on backend for historically validated orientation; application default is clockwise. Nominal preview is not a measurement of physical ink. No validated GPIO45 power rail-off/deep sleep or DS18B20.

## Build and optional flash (operator only)

Install ESP-IDF and the ESP32-S3 toolchain via [Espressif's official getting-started instructions](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/get-started/index.html), then activate that installation's `export.sh` so `IDF_PATH` and `idf.py` resolve. The complete production application, including the panel driver, was locally built with **ESP-IDF v5.5.5, Python 3.12 and target esp32s3** for this source publication. No new hardware test or flash was performed. The commands below reproduce a build and describe an optional operator-controlled flash:

```sh
# from repository root in an activated ESP-IDF shell
idf.py -C firmware/esp-idf-production set-target esp32s3
idf.py -C firmware/esp-idf-production build
# inspect firmware/esp-idf-production/build/flasher_args.json and partition-table.bin
# ONLY after checking actual board, port, backup, generated offsets and recovery plan:
idf.py -C firmware/esp-idf-production -p /dev/<your-board> flash monitor
```

If your shell selects a newer unrelated Python and `export.sh` reports a missing IDF virtual environment, use the Python environment installed by the matching IDF installer: set `IDF_PYTHON_ENV_PATH` to that environment and prepend its `bin` directory to `PATH` before sourcing `export.sh`. Do not reuse a venv from a different IDF version.

No flash is performed for this source task. Use generated `build/flasher_args.json` for bootloader offset (do **not** copy generic ESP32 offsets). Factory 16 MiB `partitions.csv`: NVS 0x9000/0x6000, phy_init 0xF000/0x1000, factory app 0x10000/0x300000, coredump 0x310000/0x20000; verify generated partition table/flash size before any flash. `partitions.proposed.csv` is not a shipped OTA layout. Factory layout has no OTA slots; signed OTA/secure boot are not implemented. Changing layout can destroy NVS and recovery state.

On missing settings or SW4 held at boot, firmware offers a temporary WPA2 SoftAP with MAC-derived SSID/password and setup page at `192.168.4.1`; enter Wi-Fi credentials, private backend URL and device bearer token in physically controlled conditions. MAC-derived credentials are not a security boundary. Default poll interval is 60000 ms; device verifies manifest/frame, stages SD, refreshes, reports status and retains last-known-good. Never compile secrets into binaries. `/api/v1/device/status` reports device ACK/errors; after interrupted power/network or bad SD, inspect this endpoint and SD state before resetting NVS.

## Host verification

From repo root: `cmake -S firmware -B firmware/build && cmake --build firmware/build && ctest --test-dir firmware/build --output-on-failure`. Repeat the configure/build/ctest cycle for `firmware/esp-idf/components/device_state/host_test`, `net_manager/host_test`, `provisioning/host_test`, `frame_store/host_tests`; `backend_client/host_tests` needs ESP-IDF cJSON via `IDF_PATH`. Host tests cannot prove panel timing, pin wiring, power integrity, colors or boot recovery. Verify on actual board before rollout: supply and SPI/BUSY, physical color/orientation, SD removal, power loss, connectivity and SW4 recovery. [Deployment/backups](../deploy/README.md).
