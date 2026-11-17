// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <atomic>

#include "common/types.h"

namespace Vulkan {

/// bbport: guest write faults waiting for the GPU command thread (Liverpool::SendFaultResolve).
/// Its wait loops (DrawPipe::Drain, Scheduler::WaitHostCopies/WaitDeferredSignals) serve them
/// through this hook instead of leaving the writing game thread blocked until they end.
struct FaultService {
    static inline std::atomic<u32> pending{0};
    static inline void (*serve)(void* context, bool run_here) = nullptr;
    static inline void* context = nullptr;
    /// `run_here`: the caller is at a point where the GPU command thread may run a fault itself
    /// when no draws are in flight (between packets); else faults only go to the draw recording
    /// thread (and the rest wait for the next ProcessCommands).
    static void Poll(bool run_here) {
        if (pending.load(std::memory_order_acquire) != 0 && serve) {
            serve(context, run_here);
        }
    }
};

} // namespace Vulkan
