# Provenance and third-party notices

The repository's root `LICENSE` (MIT) covers independently owned project source only. It does not license third-party source, vendor command tables, model weights, images, upstream dependencies or hardware documentation. Inclusion of a file here is not a representation that third-party redistribution rights have been cleared. No separate vendor license is supplied in the inspected sample corpus.

## GDEP133C02 driver (included with unresolved rights)

The following three project component files are included at the owner's explicit request:

- `firmware/esp-idf/components/epd_gdep133c02/epd_gdep133c02.c`
- `firmware/esp-idf/components/epd_gdep133c02/include/epd_gdep133c02.h`
- `firmware/esp-idf/components/epd_gdep133c02/CMakeLists.txt`

The implementation's panel initialization / command and byte sequences were sourced or adapted from the Good Display GDEP133C02 ESP32-133C02 board sample (notably AN_TM, CMD66, PSR, PWR and booster configuration). Reference in the inspected board corpus: `GDEP133C02-ESP32 code example.zip` → `GDEP133C02/main/GDEP133C02.c`. Vendor references: [GDEP133C02 display](https://www.good-display.com/product/559.html) and [ESP32-133C02 controller](https://www.good-display.com/product/574.html); seek the GDEP133C02/ESP32-133C02 board documentation and sample directly from the manufacturer and confirm the exact applicable terms. A redistributable grant/license for these sequences was **not established** in the inspected corpus. They are **not covered by the root MIT grant**; their inclusion at the owner's request is not vendor permission. A downstream publisher must independently resolve redistribution/notice requirements with the rights holder. Do not infer that buildability or an MIT header establishes ownership of vendor material.

## Converter and other dependencies

`src/eink_frame/converter.py` is the project's independently developed implementation of the hardware six-color format, row packing and dithering compatibility with the Good Display Floyd tool. Functional/output equivalence alone is not evidence of source-code copying. This is not an absolute legal-clearance opinion or a grant of rights to any vendor tool or data. Keep the project's code separate from the vendor archives and review any redistribution obligations independently.

Python dependencies are resolved in `uv.lock`, frontend dependencies in `frontend/package-lock.json` and carry their own licenses; ESP-IDF and ComfyUI are separately supplied under their respective terms. Qwen-Image-2512, FLUX.2 Klein 4B and Z-Image Turbo weights are not distributed: obtain model files and their licenses from each publisher. No vendor archive, photo, model weight or firmware binary is part of the source release. Review bundled frontend/dependency notices before redistributing generated bundles.
