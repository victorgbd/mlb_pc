// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <cmath>
#include <cstdio>
#include <cstdlib>

namespace Vulkan::SceneResolution {
// The game's scene size: 1920x1080, or BB_MLB_RES=WxH (runtime_display.c gives MLB another size, e.g.
// an ultrawide shape). Size{} is this native size everywhere below.
struct NativeDims {
    uint32_t width, height;
};
inline const NativeDims& Native() {
    static const NativeDims dims = [] {
        unsigned w = 0, h = 0;
        const char* env = std::getenv("BB_MLB_RES");
        if (env && std::sscanf(env, "%ux%u", &w, &h) == 2 && w >= 640 && h >= 360 && w <= 7680 &&
            h <= 4320) {
            return NativeDims{(w + 4) & ~7u, (h + 4) & ~7u}; // as runtime_display.c rounds it

        }
        return NativeDims{1920, 1080};
    }();
    return dims;
}
struct Size {
    uint32_t width = Native().width, height = Native().height;
    bool operator==(const Size&) const = default;
};
inline Size ForPreset(int preset, Size output = {}) {
    // Even dimensions for half-resolution effects; match the preset labels exactly.
    constexpr std::array<double, 5> scales{1.0, 1.5, 1.7, 2.0, 3.0};
    const double scale = scales[std::clamp(preset, 0, 4)];
    return {uint32_t(std::max(1l, std::lround(output.width / scale / 2)) * 2),
            uint32_t(std::max(1l, std::lround(output.height / scale / 2)) * 2)};
}
inline Size ForScale(int percent, Size output = {}) {
    // Multiples of 8: the half-resolution chain (and its mips) stays even.
    return {uint32_t(std::max(1l, std::lround(output.width * percent / 800.0)) * 8),
            uint32_t(std::max(1l, std::lround(output.height * percent / 800.0)) * 8)};
}
// bbport BB_RENDER_SCALE=<percent 25-99> or <W>x<H> (even, below the native size): the native scene
// renders at that size and FSR 1 (EASU + RCAS) rebuilds it before the game's final composite
// (SceneTargets::UpscaleColors). For games without a temporal upscaler hook (MLB). Unset, 100 or
// the native size: native.
inline Size SpatialSize() {
    static const Size size = [] {
        const char* env = std::getenv("BB_RENDER_SCALE");
        if (!env) return Size{};
        unsigned w = 0, h = 0;
        if (std::sscanf(env, "%ux%u", &w, &h) == 2) {
            if (w >= 480 && h >= 270 && w <= Native().width && h <= Native().height && w % 2 == 0 &&
                h % 2 == 0) {
                return Size{w, h};
            }
            return Size{};
        }
        const int percent = std::atoi(env);
        return percent >= 25 && percent < 100 ? ForScale(percent) : Size{};
    }();
    return size;
}
inline bool SpatialScaling() { return SpatialSize() != Size{}; }
constexpr uint32_t Pack(Size size) { return size.width | (size.height << 16); }
constexpr Size Unpack(uint32_t packed) {
    return packed ? Size{packed & 65535u, packed >> 16} : Size{};
}
// A proxy and the guest-size image hold the same logical surface. Native shader reads
// need a resolve after proxy writes; native writes invalidate the cached proxy.
struct Coherence {
    bool valid = false, dirty = false;
    void ProxyWrite() { valid = dirty = true; }
    void CopiedToProxy() { valid = true; dirty = false; }
    void Resolved() { dirty = false; }
    void NativeWrite() { valid = dirty = false; }
};
} // namespace Vulkan::SceneResolution
