# frame_store

Crash-safe microSD storage for 1200x1600 4bpp frames. The caller streams to
`FRAME_STORE_DOWNLOAD_PATH` and closes it before validation/promotion.

Promotion first reopens and verifies exactly 960000 bytes plus SHA-256. It then
persists a dual-slot CRC journal in staged state before rotating
`current.bin` to `previous.bin` and renaming `download.tmp` to `current.bin`.
This ordering leaves a SHA-verifiable known-good frame at every FAT rename
boundary. Recovery prefers a valid current frame, then a valid previous frame.

`frame_store_mark_displayed()` is deliberately separate: call it only after a
successful panel refresh, passing the hash of the frame actually refreshed.

Host tests:

```sh
cmake -S host_tests -B host_tests/build
cmake --build host_tests/build
ctest --test-dir host_tests/build --output-on-failure
```
