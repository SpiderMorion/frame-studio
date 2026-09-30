# Host firmware core

C++20 host tests (no ESP-IDF required):

```sh
cmake -S firmware -B firmware/build -DCMAKE_BUILD_TYPE=Release
cmake --build firmware/build
ctest --test-dir firmware/build --output-on-failure
```

The core validates the packed frame contract, immutable manifest, two-slot CRC journal and pure state transitions; it does not talk to hardware. More host C tests exist under `firmware/esp-idf/components/*/host_test[s]`. ESP-IDF factory build, physical panel validation and historical firmware v0.1.1 are described in `docs/firmware.md`. The included panel component has vendor-derived command sequences whose redistribution rights are not established; see `THIRD_PARTY_NOTICES.md`. Factory app partitions are in `firmware/esp-idf-production/partitions.csv`; proposed OTA layout is not deployed and signed OTA is not implemented.
