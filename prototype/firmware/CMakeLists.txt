# CMakeLists.txt — Zephyr application build for the clamp-on thermal leak
# sensor (application A1).
#
# NOT RUN HERE. There is no cmake and no west on the machine this was written
# on; see README.md for the exact build commands and for what that means.
#
# cmake_minimum_required 3.20.0 is Zephyr's own floor for v3.x/v4.x.

cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(leakguard_a1 LANGUAGES C)

target_sources(app PRIVATE
  src/main.c
  src/leak_detect.c
  src/uplink.c
  src/valve.c
  src/install_probe.c
)

target_include_directories(app PRIVATE src)

# Four of the five sources above have no Zephyr dependency at all: leak_detect.c,
# uplink.c, valve.c and install_probe.c include nothing but <stdint.h>,
# <stdbool.h> and <stddef.h>. That is what lets tests/ compile them against a
# host compiler with no RTOS, and it is a property worth protecting — if a
# `#include <zephyr/...>` ever appears in one of them, the host tests stop
# building and the byte-exactness of the §1 payload stops being checkable
# against the Node codec and the Python backend.
#
# tests/ is deliberately NOT wired into this CMake build. It is a plain-C
# harness with its own Makefile, because the point of it is to run on a
# developer's machine (or in CI) without a cross toolchain or a Zephyr tree.
