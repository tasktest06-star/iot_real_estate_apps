# tests/Makefile — host build for the four pure modules.
#
# THIS HAS NEVER BEEN RUN. There is no cc, gcc, clang, cmake or make-with-a-
# compiler on the machine these tests were written on. See README.md in this
# directory. No claim is made that these tests pass.
#
# It needs nothing but a C11 compiler and make. No Zephyr tree, no
# arm-none-eabi, no CMake, no test framework, no network.
#
#     make        # build the four suites
#     make run    # build and run them, non-zero exit on any failure
#     make STRICT=1 run
#     make clean

CC      ?= cc
SRC      = ../src
CFLAGS  ?= -std=c11 -O1 -g -I$(SRC) \
           -Wall -Wextra -Wshadow -Wundef -Wpointer-arith -Wcast-qual \
           -Wstrict-prototypes -Wmissing-prototypes -Wno-unused-parameter

# -Werror is opt-in rather than default. These sources have never been through
# a compiler, so the first run is overwhelmingly likely to surface warnings
# (sign-compare and conversion warnings especially, on a file full of int32
# arithmetic). Fix them with STRICT=1 once the suite builds; having -Werror on
# by default would just mean nobody can run anything until every last warning is
# gone, which is the wrong order to do the work in.
ifeq ($(STRICT),1)
CFLAGS += -Werror -Wconversion -Wsign-conversion
endif

# Sanitisers are worth it here: the detector does ring-buffer index arithmetic
# and the codec does pointer arithmetic into a fixed-size byte array, which are
# exactly the two things ASan and UBSan catch for free.
ifeq ($(SAN),1)
CFLAGS  += -fsanitize=address,undefined -fno-omit-frame-pointer
LDFLAGS += -fsanitize=address,undefined
endif

SUITES = test_uplink test_leak_detect test_valve test_install_probe

.PHONY: all run clean

all: $(SUITES)

test_uplink: test_uplink.c $(SRC)/uplink.c $(SRC)/uplink.h minitest.h
	$(CC) $(CFLAGS) -o $@ test_uplink.c $(SRC)/uplink.c $(LDFLAGS)

test_leak_detect: test_leak_detect.c $(SRC)/leak_detect.c $(SRC)/leak_detect.h minitest.h
	$(CC) $(CFLAGS) -o $@ test_leak_detect.c $(SRC)/leak_detect.c $(LDFLAGS)

# valve.c includes install_probe.h for install_probe_t, so the suite links both.
test_valve: test_valve.c $(SRC)/valve.c $(SRC)/install_probe.c $(SRC)/valve.h minitest.h
	$(CC) $(CFLAGS) -o $@ test_valve.c $(SRC)/valve.c $(SRC)/install_probe.c $(LDFLAGS)

test_install_probe: test_install_probe.c $(SRC)/install_probe.c $(SRC)/install_probe.h minitest.h
	$(CC) $(CFLAGS) -o $@ test_install_probe.c $(SRC)/install_probe.c $(LDFLAGS)

run: $(SUITES)
	@rc=0; for t in $(SUITES); do \
		echo "--- $$t"; ./$$t || rc=1; \
	done; \
	if [ $$rc -eq 0 ]; then echo "ALL SUITES PASSED"; else echo "FAILURES"; fi; \
	exit $$rc

clean:
	rm -f $(SUITES)
