# ESP-IDF component placeholder

This directory reserves the adapter boundary for the host-tested `firmware_core` library. It intentionally contains no ESP-IDF `CMakeLists.txt`: the ESP-IDF version, target integration, storage adapter, and hardware dependencies have not been established or built.

Do not add panel GPIO, SPI timing, BUSY polarity, controller split, or palette codes here until they are confirmed from the exact GDEP133C02 vendor driver and board schematic.
