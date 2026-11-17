// SPDX-License-Identifier: GPL-2.0-or-later
// bbport: BB_WAIT_STATS=1, time guest threads spend blocked in a wait (src/runtime_waitstats.c).
#pragma once

#include <cstdint>

extern "C" std::uint64_t runtime_wait_begin(void);
extern "C" void runtime_wait_end(const char* kind, std::uint64_t start);

struct BbWaitScope {
    const char* kind;
    std::uint64_t start;
    explicit BbWaitScope(const char* kind_) : kind{kind_}, start{runtime_wait_begin()} {}
    ~BbWaitScope() {
        runtime_wait_end(kind, start);
    }
};
