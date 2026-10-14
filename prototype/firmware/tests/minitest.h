/*
 * minitest.h — a test harness small enough to read in one sitting.
 *
 * WHY NOT ZTEST: ztest needs a Zephyr tree and either native_sim or a board.
 * The whole point of these tests is that the four pure modules
 * (uplink.c, leak_detect.c, valve.c, install_probe.c) can be checked with
 * nothing but a C compiler — which is what makes it possible to diff the
 * 8-byte payload encoder against the Node codec and the Python backend without
 * standing up an embedded toolchain. Adding a framework dependency would
 * defeat that.
 *
 * WHY NOT A FRAMEWORK AT ALL: there is no package manager in play here and the
 * assertions needed are "these two integers are equal" and "these two strings
 * are equal". Forty lines of macros is the right size.
 *
 * NOT RUN HERE. There is no cc, gcc or clang on the machine this was written
 * on. See tests/README.md — these tests have never been executed and no claim
 * is made that they pass.
 */

#ifndef MINITEST_H_
#define MINITEST_H_

#include <stdio.h>
#include <string.h>

static int mt_checks;
static int mt_fails;
static const char *mt_case = "-";

/* Name the behaviour under test. Appears in every failure line, so a failure
 * report says which contract clause broke rather than which line number. */
#define MT_CASE(name) (mt_case = (name))

#define MT_TRUE(cond)                                                                        \
	do {                                                                                 \
		mt_checks++;                                                                 \
		if (!(cond)) {                                                               \
			mt_fails++;                                                          \
			printf("FAIL [%s] %s:%d: %s\n", mt_case, __FILE__, __LINE__, #cond); \
		}                                                                            \
	} while (0)

#define MT_EQI(got, want)                                                                    \
	do {                                                                                 \
		long long g_ = (long long)(got);                                             \
		long long w_ = (long long)(want);                                           \
		mt_checks++;                                                                 \
		if (g_ != w_) {                                                              \
			mt_fails++;                                                          \
			printf("FAIL [%s] %s:%d: %s == %lld, expected %lld\n", mt_case,      \
			       __FILE__, __LINE__, #got, g_, w_);                            \
		}                                                                            \
	} while (0)

#define MT_EQS(got, want)                                                                    \
	do {                                                                                 \
		const char *g_ = (got);                                                      \
		const char *w_ = (want);                                                     \
		mt_checks++;                                                                 \
		if ((g_ == NULL) || (w_ == NULL) || (strcmp(g_, w_) != 0)) {                  \
			mt_fails++;                                                          \
			printf("FAIL [%s] %s:%d: %s == \"%s\", expected \"%s\"\n", mt_case,  \
			       __FILE__, __LINE__, #got, (g_ != NULL) ? g_ : "(null)",        \
			       (w_ != NULL) ? w_ : "(null)");                                 \
		}                                                                            \
	} while (0)

/* Compares a packed payload against an expected byte string and prints both in
 * hex on mismatch — the only genuinely useful failure output for a codec. */
#define MT_EQBYTES(got, want, len)                                                           \
	do {                                                                                 \
		size_t i_;                                                                   \
		mt_checks++;                                                                 \
		if (memcmp((got), (want), (len)) != 0) {                                      \
			mt_fails++;                                                          \
			printf("FAIL [%s] %s:%d: bytes differ\n  got:  ", mt_case, __FILE__,  \
			       __LINE__);                                                    \
			for (i_ = 0; i_ < (size_t)(len); i_++) {                             \
				printf("%02X ", ((const unsigned char *)(got))[i_]);          \
			}                                                                    \
			printf("\n  want: ");                                                \
			for (i_ = 0; i_ < (size_t)(len); i_++) {                             \
				printf("%02X ", ((const unsigned char *)(want))[i_]);         \
			}                                                                    \
			printf("\n");                                                        \
		}                                                                            \
	} while (0)

static int mt_report(const char *suite)
{
	printf("%s: %d checks, %d failures\n", suite, mt_checks, mt_fails);
	return (mt_fails == 0) ? 0 : 1;
}

#endif /* MINITEST_H_ */
