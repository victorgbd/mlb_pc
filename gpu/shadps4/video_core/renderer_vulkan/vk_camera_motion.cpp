// SPDX-License-Identifier: GPL-2.0-or-later
#include <algorithm>
#include "video_core/renderer_vulkan/vk_camera_motion.h"

#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "bbport_toggles.h"

#include "video_core/host_shaders/camera_motion_comp.h"
#include "video_core/host_shaders/camera_motion_debug_comp.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_object_motion.h"
#include "video_core/renderer_vulkan/vk_runtime.h"
#include "video_core/renderer_vulkan/scene_resolution.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"
#include "video_core/renderer_vulkan/vk_shader_util.h"
#include "video_core/texture_cache/texture_cache.h"

namespace Vulkan {

namespace {

struct PushConstants {
    std::array<float, 12> reproject;
    std::array<float, 4> proj;
    std::array<float, 4> prev_proj;
    std::array<float, 2> size;
    std::array<float, 2> jitter;
    std::array<float, 2> previous_jitter;
    u32 mode;
};

/// a * b for 3x4 affine matrices (rows [R | t]).
std::array<float, 12> Multiply(const std::array<float, 12>& a, const std::array<float, 12>& b) {
    std::array<float, 12> out{};
    for (int r = 0; r < 3; ++r) {
        for (int c = 0; c < 4; ++c) {
            float v = c == 3 ? a[r * 4 + 3] : 0.0f;
            for (int k = 0; k < 3; ++k) {
                v += a[r * 4 + k] * b[k * 4 + c];
            }
            out[r * 4 + c] = v;
        }
    }
    return out;
}

/// Inverse of a 4x4 matrix (rows); false when singular.
bool Invert(const std::array<double, 16>& m, std::array<double, 16>& out) {
    std::array<double, 32> a{};
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            a[r * 8 + c] = m[r * 4 + c];
        }
        a[r * 8 + 4 + r] = 1.0;
    }
    for (int c = 0; c < 4; ++c) {
        int pivot = c;
        for (int r = c + 1; r < 4; ++r) {
            if (std::abs(a[r * 8 + c]) > std::abs(a[pivot * 8 + c])) pivot = r;
        }
        if (std::abs(a[pivot * 8 + c]) < 1e-12) return false;
        if (pivot != c) {
            for (int k = 0; k < 8; ++k) std::swap(a[c * 8 + k], a[pivot * 8 + k]);
        }
        const double inv = 1.0 / a[c * 8 + c];
        for (int k = 0; k < 8; ++k) a[c * 8 + k] *= inv;
        for (int r = 0; r < 4; ++r) {
            if (r == c) continue;
            const double f = a[r * 8 + c];
            if (f == 0.0) continue;
            for (int k = 0; k < 8; ++k) a[r * 8 + k] -= f * a[c * 8 + k];
        }
    }
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            out[r * 4 + c] = a[r * 8 + 4 + c];
        }
    }
    return true;
}

} // namespace

CameraMotion::CameraMotion(const Instance& instance_, Scheduler& scheduler_,
                           VideoCore::TextureCache& texture_cache_, Runtime& runtime_)
    : instance{instance_}, scheduler{scheduler_}, texture_cache{texture_cache_}, runtime{runtime_} {
    const char* env = std::getenv("BB_DEBUG_MOTION");
    debug_overlay = env && env[0] == '1';
    const char* upscaler = std::getenv("BB_UPSCALER");
    // The upscaler can be switched on from the menu at any time: the camera is always tracked
    // unless BB_UPSCALER=none.
    for_upscaler = !(upscaler && std::strcmp(upscaler, "none") == 0);
    for (const char* list = std::getenv("BB_VELOCITY_PS"); list && *list;) {
        char* end = nullptr;
        const u64 hash = std::strtoull(list, &end, 16);
        if (end == list) {
            break;
        }
        velocity_shaders.push_back(u64(u32(hash)));
        list = *end == ',' ? end + 1 : end;
    }
    velocity_mode = for_upscaler && !velocity_shaders.empty();
    if (velocity_mode) {
        std::printf("Camera motion: from the game's velocity pass (%zu shaders)\n",
                    velocity_shaders.size());
    }
    const char* temporal = std::getenv("BB_MLB_TEMPORAL");
    vp_mode = for_upscaler && !velocity_mode && temporal && temporal[0] == '1';
    if (vp_mode) {
        std::printf("Camera motion: from the scene's view-projection matrix (MLB)\n");
    }
    const auto device = instance.GetDevice();
    if (for_upscaler) {
        const std::array<vk::DescriptorSetLayoutBinding, 3> motion_bindings = {{
            {.binding = 0,
             .descriptorType = vk::DescriptorType::eSampledImage,
             .descriptorCount = 1,
             .stageFlags = vk::ShaderStageFlagBits::eCompute},
            {.binding = 1,
             .descriptorType = vk::DescriptorType::eStorageImage,
             .descriptorCount = 1,
             .stageFlags = vk::ShaderStageFlagBits::eCompute},
            {.binding = 2,
             .descriptorType = vk::DescriptorType::eSampledImage,
             .descriptorCount = 1,
             .stageFlags = vk::ShaderStageFlagBits::eCompute},
        }};
        motion_desc_layout = Check(device.createDescriptorSetLayoutUnique({
            .flags = vk::DescriptorSetLayoutCreateFlagBits::ePushDescriptorKHR,
            .bindingCount = static_cast<u32>(motion_bindings.size()),
            .pBindings = motion_bindings.data(),
        }));
        const vk::PushConstantRange range{.stageFlags = vk::ShaderStageFlagBits::eCompute,
                                          .offset = 0,
                                          .size = sizeof(PushConstants)};
        motion_pipeline_layout = Check(device.createPipelineLayoutUnique({
            .setLayoutCount = 1,
            .pSetLayouts = &*motion_desc_layout,
            .pushConstantRangeCount = 1,
            .pPushConstantRanges = &range,
        }));
        const auto motion_module = CompileSPV(CAMERA_MOTION_COMP, device);
        motion_pipeline = Check(device.createComputePipelineUnique(
            {}, vk::ComputePipelineCreateInfo{
                    .stage = {.stage = vk::ShaderStageFlagBits::eCompute,
                              .module = motion_module,
                              .pName = "main"},
                    .layout = *motion_pipeline_layout,
                }));
        device.destroyShaderModule(motion_module);
    }
    if (!debug_overlay) {
        return;
    }
    const std::array<vk::DescriptorSetLayoutBinding, 4> bindings = {{
        {.binding = 0,
         .descriptorType = vk::DescriptorType::eSampledImage,
         .descriptorCount = 1,
         .stageFlags = vk::ShaderStageFlagBits::eCompute},
        {.binding = 1,
         .descriptorType = vk::DescriptorType::eStorageImage,
         .descriptorCount = 1,
         .stageFlags = vk::ShaderStageFlagBits::eCompute},
        {.binding = 2,
         .descriptorType = vk::DescriptorType::eStorageBuffer,
         .descriptorCount = 1,
         .stageFlags = vk::ShaderStageFlagBits::eCompute},
        {.binding = 3,
         .descriptorType = vk::DescriptorType::eStorageBuffer,
         .descriptorCount = 1,
         .stageFlags = vk::ShaderStageFlagBits::eCompute},
    }};
    desc_layout = Check(device.createDescriptorSetLayoutUnique({
        .flags = vk::DescriptorSetLayoutCreateFlagBits::ePushDescriptorKHR,
        .bindingCount = static_cast<u32>(bindings.size()),
        .pBindings = bindings.data(),
    }));
    const vk::PushConstantRange push_range{
        .stageFlags = vk::ShaderStageFlagBits::eCompute,
        .offset = 0,
        .size = sizeof(PushConstants),
    };
    pipeline_layout = Check(device.createPipelineLayoutUnique({
        .setLayoutCount = 1,
        .pSetLayouts = &*desc_layout,
        .pushConstantRangeCount = 1,
        .pPushConstantRanges = &push_range,
    }));
    const auto module = CompileSPV(CAMERA_MOTION_DEBUG_COMP, device);
    overlay_pipeline = Check(device.createComputePipelineUnique(
        {}, vk::ComputePipelineCreateInfo{
                .stage = {.stage = vk::ShaderStageFlagBits::eCompute,
                          .module = module,
                          .pName = "main"},
                .layout = *pipeline_layout,
            }));
    device.destroyShaderModule(module);
    for (auto& frame : frames) {
        frame = std::make_unique<VideoCore::Buffer>(instance, 0, 3840ull * 2160 * 4,
                                                    VideoCore::MemoryType::DeviceLocal);
    }
    std::printf("GPU: camera motion debug overlay on\n");
}

CameraMotion::~CameraMotion() = default;

float CameraMotion::VerticalFov() const noexcept {
    if (current.proj[1] == 0.0f) {
        return 1.0f; // no camera constants (velocity mode): a typical 57 degrees
    }
    return 2.0f * std::atan(1.0f / current.proj[1]);
}

float CameraMotion::Far() const noexcept {
    if (vp_mode && vp_far > 0.0f) return vp_far;
    return velocity_mode ? 10000.0f : 3000.0f;
}

void CameraMotion::OnViewProjection(const float* m, bool y_up) {
    if (!vp_mode || vp_frame.valid) {
        return; // the first one of a frame is the main camera
    }
    // Column-major: element (row r, column c) at m[c * 4 + r].
    std::array<double, 16> rows{};
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            rows[r * 4 + c] = m[c * 4 + r];
        }
    }
    const auto norm3 = [&](int r) {
        return std::sqrt(rows[r * 4] * rows[r * 4] + rows[r * 4 + 1] * rows[r * 4 + 1] +
                         rows[r * 4 + 2] * rows[r * 4 + 2]);
    };
    // A perspective camera: row w is the unit view direction (w = view depth), and rows x and y
    // scale by the aspect (|x| / |y| = height / width of the game's size).
    const double nx = norm3(0), ny = norm3(1), nw = norm3(3);
    const auto& native = SceneResolution::Native();
    const double aspect = double(native.height) / native.width;
    if (!std::isfinite(nx + ny + nw) || std::abs(nw - 1.0) > 1e-3 || ny < 0.1 ||
        std::abs(nx / ny / aspect - 1.0) > 0.02) {
        return;
    }
    // Row z = a row w + b (depth = a + b / w): a D3D projection, near -b/a, far b/(1-a).
    const double a = rows[8] * rows[12] + rows[9] * rows[13] + rows[10] * rows[14];
    const double b = rows[11] - a * rows[15];
    const double residual = std::abs(rows[8] - a * rows[12]) + std::abs(rows[9] - a * rows[13]) +
                            std::abs(rows[10] - a * rows[14]);
    if (residual > 1e-3 * std::abs(a) || a == 0.0 || b >= 0.0) {
        return;
    }
    vp_frame.rows = rows;
    vp_frame.valid = true;
    current.proj = {float(nx), float(ny), float(a), float(b)}; // Near() and VerticalFov()
    vp_far = a > 1.0 ? float(b / (1.0 - a)) : 0.0f;
    static bool reported = false;
    if (!reported) {
        reported = true;
        std::printf("Camera motion: view-projection camera found (y scale %.3f, near %.2f, far "
                    "%.1f, clip y %s)\n",
                    ny, -b / a, vp_far, y_up ? "up" : "down");
    }
    // BB_CAMERA_LOG=1: each frame's camera (y scale, view direction row, last frame's known).
    static const bool camera_log = std::getenv("BB_CAMERA_LOG") != nullptr;
    if (camera_log) {
        std::printf("Camera VP: y %.3f w (%.4f %.4f %.4f %.2f) previous %d\n", ny, rows[12],
                    rows[13], rows[14], rows[15], int(vp_last.valid));
    }
    if (!vp_last.valid) {
        return;
    }
    // View position (w ndc.x, w ndc.y, w, 1) -> current clip (N) -> world -> previous clip.
    std::array<double, 16> inverse{};
    if (!Invert(rows, inverse)) {
        return;
    }
    std::array<double, 16> reproject{};
    for (int r = 0; r < 4; ++r) {
        for (int c = 0; c < 4; ++c) {
            double v = 0.0;
            for (int k = 0; k < 4; ++k) v += vp_last.rows[r * 4 + k] * inverse[k * 4 + c];
            reproject[r * 4 + c] = v;
        }
    }
    // RecordVelocity's shader takes clip +y as up (previous uv.y = 0.5 - 0.5 y / w): with +y
    // down, its output row y is negated. The view position below holds the true clip y.
    const double y_sign = y_up ? 1.0 : -1.0;
    auto& camera = velocity_camera;
    constexpr std::array<int, 3> out_rows{0, 1, 3};
    for (int i = 0; i < 3; ++i) {
        const double* r = &reproject[out_rows[i] * 4];
        const double s = i == 1 ? y_sign : 1.0;
        camera.to_previous_clip[i * 4 + 0] = float(s * r[0]);
        camera.to_previous_clip[i * 4 + 1] = float(s * r[1]);
        camera.to_previous_clip[i * 4 + 2] = float(s * (a * r[2] + r[3]));
        camera.to_previous_clip[i * 4 + 3] = float(s * b * r[2]);
    }
    // View x = z (2u - 1), view y = z (1 - 2v) with clip +y up (or z (2v - 1)); z = b / (depth - a).
    camera.uv_to_view = {2.0f, float(-2.0 * y_sign), -1.0f, float(y_sign)};
    camera.depth_to_z = {float(a), float(b)};
    camera.valid = std::all_of(camera.to_previous_clip.begin(), camera.to_previous_clip.end(),
                               [](float v) { return std::isfinite(v); });
}

float CameraMotion::Near() const noexcept {
    if (current.proj[2] == 0.0f) {
        return 0.1f;
    }
    // depth = zs + zo / z is 0 at the near plane.
    return -current.proj[3] / current.proj[2];
}

std::array<std::array<float, 4>, 3> CameraMotion::TaaDepthParameters() const noexcept {
    const auto transform = Multiply(previous.view, current.inv_view);
    return {current.proj, previous.proj,
            {transform[8], transform[9], transform[10], transform[11]}};
}

vk::ImageView CameraMotion::ObjectMotionView() const noexcept {
    return object_motion && object_motion->Enabled() ? object_motion->View() : vk::ImageView{};
}

vk::Image CameraMotion::ObjectMotionImage(u32 width, u32 height) const noexcept {
    return object_motion && object_motion->Enabled() ? object_motion->Image(width, height)
                                                      : vk::Image{};
}

void CameraMotion::RecordMotion(vk::CommandBuffer cmdbuf, vk::ImageView depth_view,
                                vk::ImageView motion_view, u32 width, u32 height) {
    if (vp_mode) {
        // MLB: the camera from the view-projection matrices, object vectors where drawn.
        bool objects_valid = false;
        const vk::ImageView objects =
            object_motion && object_motion->Enabled()
                ? object_motion->PrepareRead(cmdbuf, width, height, objects_valid)
                : vk::ImageView{};
        RecordVelocity(cmdbuf, depth_view, motion_view, width, height,
                       objects_valid ? objects : vk::ImageView{});
        return;
    }
    bool object_valid = false;
    const vk::ImageView object_view = object_motion && object_motion->Enabled()
        ? object_motion->PrepareRead(cmdbuf, width, height, object_valid) : depth_view;
    const PushConstants push{
        .reproject = Multiply(previous.view, current.inv_view),
        .proj = current.proj,
        .prev_proj = previous.proj,
        .size = {float(width), float(height)},
        .jitter = jitter,
        .previous_jitter = previous_jitter,
        .mode = object_valid ? 1u : 0u,
    };
    const vk::DescriptorImageInfo depth_info{.imageView = depth_view,
                                             .imageLayout = vk::ImageLayout::eGeneral};
    const vk::DescriptorImageInfo motion_info{.imageView = motion_view,
                                              .imageLayout = vk::ImageLayout::eGeneral};
    const vk::DescriptorImageInfo object_info{.imageView = object_view,
                                              .imageLayout = vk::ImageLayout::eGeneral};
    const std::array<vk::WriteDescriptorSet, 3> writes = {{
        {.dstBinding = 0,
         .descriptorCount = 1,
         .descriptorType = vk::DescriptorType::eSampledImage,
         .pImageInfo = &depth_info},
        {.dstBinding = 1,
         .descriptorCount = 1,
         .descriptorType = vk::DescriptorType::eStorageImage,
         .pImageInfo = &motion_info},
        {.dstBinding = 2,
         .descriptorCount = 1,
         .descriptorType = vk::DescriptorType::eSampledImage,
         .pImageInfo = &object_info},
    }};
    cmdbuf.bindPipeline(vk::PipelineBindPoint::eCompute, *motion_pipeline);
    cmdbuf.pushDescriptorSetKHR(vk::PipelineBindPoint::eCompute, *motion_pipeline_layout, 0,
                                writes);
    cmdbuf.pushConstants(*motion_pipeline_layout, vk::ShaderStageFlagBits::eCompute, 0,
                         sizeof(push), &push);
    cmdbuf.dispatch((width + 7) / 8, (height + 7) / 8, 1);
}

bool CameraMotion::IsVelocityShader(u64 ps_hash) const noexcept {
    return std::find(velocity_shaders.begin(), velocity_shaders.end(), u64(u32(ps_hash))) !=
           velocity_shaders.end();
}

// bbport: the heading (degrees) of the camera the game last rendered with, and when (seconds,
// steady clock).
static std::atomic<float> shown_camera_yaw{0.0f};
static std::atomic<double> shown_camera_time{0.0};

extern "C" int bbgpu_camera_yaw(float* yaw_degrees, double* age_seconds) {
    const double t = shown_camera_time.load(std::memory_order_acquire);
    if (t == 0.0) {
        return 0;
    }
    *yaw_degrees = shown_camera_yaw.load(std::memory_order_relaxed);
    *age_seconds = std::chrono::duration<double>(
                       std::chrono::steady_clock::now().time_since_epoch()).count() - t;
    return 1;
}

void CameraMotion::OnVelocityConstants(const float* c, u32 dwords) {
    // Uncharted's camera velocity pass (its pixel shader, read from the shader dump):
    //   z = c9 / (depth - c8), view = (z (c0 u + c2), z (c1 v + c3), z)
    //   p = M view + t (M, t: rows c12..c23; pixels with stencil bits 0xc0 add c56..c58)
    //   previous clip x, y, w = rows c40..c43, c44..c47, c52..c55 applied to (p, 1)
    //   previous uv = (0.5 x / w + 0.5, 0.5 - 0.5 y / w)
    if (dwords < 60) {
        return;
    }
    auto& camera = velocity_camera;
    const float* rows[3] = {c + 40, c + 44, c + 52};
    for (int r = 0; r < 3; ++r) {
        for (int col = 0; col < 4; ++col) {
            float v = col == 3 ? rows[r][3] : 0.0f;
            for (int k = 0; k < 3; ++k) {
                v += rows[r][k] * c[12 + k * 4 + col];
            }
            camera.to_previous_clip[r * 4 + col] = v;
        }
    }
    camera.uv_to_view = {c[0], c[1], c[2], c[3]};
    camera.depth_to_z = {c[8], c[9]};
    camera.valid = c[9] != 0.0f &&
                   std::all_of(camera.to_previous_clip.begin(), camera.to_previous_clip.end(),
                               [](float v) { return std::isfinite(v); });
    // The shown camera's heading for the mouse controls (bbgpu_camera_yaw: which game camera is on
    // screen), and with BB_CAMERA_LOG=1 its direction every frame, for measuring how the game turns
    // the camera from the right stick.
    const float fx = c[14], fy = c[18], fz = c[22];
    const double t = std::chrono::duration<double>(
                         std::chrono::steady_clock::now().time_since_epoch()).count();
    if (std::isfinite(fx) && std::isfinite(fz) && (fx != 0.0f || fz != 0.0f)) {
        shown_camera_yaw.store(std::atan2(fx, fz) * 57.29578f, std::memory_order_relaxed);
        shown_camera_time.store(t, std::memory_order_release);
    }
    static const bool camera_log = std::getenv("BB_CAMERA_LOG") != nullptr;
    if (camera_log) {
        std::printf("Camera: t=%.4f yaw=%.3f pitch=%.3f\n", t,
                    std::atan2(fx, fz) * 57.29578f,
                    std::asin(std::clamp(fy / std::sqrt(fx * fx + fy * fy + fz * fz), -1.0f, 1.0f)) * 57.29578f);
    }
    // The extra motion of stencil-marked pixels is not reproduced: say when it is used.
    static bool reported = false;
    if (!reported && (c[56] != 0.0f || c[57] != 0.0f || c[58] != 0.0f)) {
        reported = true;
        std::printf("Camera motion: the velocity pass moves stencil-marked pixels by "
                    "(%g, %g, %g); not reproduced\n", c[56], c[57], c[58]);
    }
}

void CameraMotion::RecordVelocity(vk::CommandBuffer cmdbuf, vk::ImageView depth_view,
                                  vk::ImageView motion_view, u32 width, u32 height,
                                  vk::ImageView object_view) {
    const auto& camera = velocity_camera;
    const PushConstants push{
        .reproject = camera.to_previous_clip,
        .proj = camera.uv_to_view,
        .prev_proj = {camera.depth_to_z[0], camera.depth_to_z[1], 0.0f, 0.0f},
        .size = {float(width), float(height)},
        .jitter = jitter,
        .previous_jitter = previous_jitter,
        .mode = camera.valid ? (object_view ? 2u | 16u : 2u) : 3u,
    };
    const vk::DescriptorImageInfo depth_info{.imageView = depth_view,
                                             .imageLayout = vk::ImageLayout::eGeneral};
    const vk::DescriptorImageInfo motion_info{.imageView = motion_view,
                                              .imageLayout = vk::ImageLayout::eGeneral};
    // Binding 2: object motion (MLB), else not read in these modes.
    const vk::DescriptorImageInfo velocity_info{.imageView = object_view ? object_view : depth_view,
                                                .imageLayout = vk::ImageLayout::eGeneral};
    const std::array<vk::WriteDescriptorSet, 3> writes = {{
        {.dstBinding = 0,
         .descriptorCount = 1,
         .descriptorType = vk::DescriptorType::eSampledImage,
         .pImageInfo = &depth_info},
        {.dstBinding = 1,
         .descriptorCount = 1,
         .descriptorType = vk::DescriptorType::eStorageImage,
         .pImageInfo = &motion_info},
        {.dstBinding = 2,
         .descriptorCount = 1,
         .descriptorType = vk::DescriptorType::eSampledImage,
         .pImageInfo = &velocity_info},
    }};
    cmdbuf.bindPipeline(vk::PipelineBindPoint::eCompute, *motion_pipeline);
    cmdbuf.pushDescriptorSetKHR(vk::PipelineBindPoint::eCompute, *motion_pipeline_layout, 0,
                                writes);
    cmdbuf.pushConstants(*motion_pipeline_layout, vk::ShaderStageFlagBits::eCompute, 0,
                         sizeof(push), &push);
    cmdbuf.dispatch((width + 7) / 8, (height + 7) / 8, 1);
}

void CameraMotion::OnConstants(const float* data) {
    // Scene constants: far plane 3000, 1/far, and the render size.
    if (data[0] != 3000.0f || data[4] < 64.0f || data[5] < 64.0f ||
        std::abs(data[1] * data[0] - 1.0f) > 1e-3f) {
        return;
    }
    if (frame_has_camera) {
        return; // the first one of a frame is the main camera
    }
    previous = current;
    std::memcpy(current.view.data(), data + 8, 12 * sizeof(float));
    std::memcpy(current.inv_view.data(), data + 180, 12 * sizeof(float));
    current.proj = {data[52], data[57], data[62], data[63]};
    current.valid = current.proj[0] != 0.0f && current.proj[1] != 0.0f;
    const std::array<u32, 2> size{u32(data[4]), u32(data[5])};
    if (size != render_size) {
        std::printf("Camera motion: scene render size %ux%u\n", size[0], size[1]);
        render_size = size;
    }
    frame_has_camera = true;
}

void CameraMotion::OnGBufferPass(VideoCore::ImageId depth) {
    depth_id = depth;
}

void CameraMotion::OnDisplayPass(VideoCore::ImageId frame) {
    if (debug_overlay && frame && depth_id && current.valid && previous.valid) {
        Overlay(frame);
    }
    if (!frame_has_camera) InvalidateHistory();
    frame_has_camera = false;
    depth_id = {};
    velocity_camera.valid = false;
    // View-projection mode: this frame's camera is the next frame's previous one.
    vp_last = vp_frame;
    vp_frame.valid = false;
}

void CameraMotion::Overlay(VideoCore::ImageId frame) {
    auto& depth = texture_cache.GetImage(depth_id);
    auto& color = texture_cache.GetImage(frame);
    const auto depth_format = depth.info.pixel_format;
    if ((depth_format != vk::Format::eD32Sfloat && depth_format != vk::Format::eD32SfloatS8Uint) ||
        color.info.pixel_format != vk::Format::eR8G8B8A8Unorm ||
        !(color.usage_flags & vk::ImageUsageFlagBits::eStorage) ||
        color.info.size.width != depth.info.size.width ||
        color.info.size.height != depth.info.size.height) {
        static bool warned = false;
        if (!warned) {
            warned = true;
            std::printf("Camera motion: overlay skipped (depth %s, frame %s %ux%u)\n",
                        vk::to_string(depth_format).c_str(),
                        vk::to_string(color.info.pixel_format).c_str(), color.info.size.width,
                        color.info.size.height);
        }
        return;
    }
    const auto device = instance.GetDevice();
    const auto depth_view = Check(device.createImageView({
        .image = vk::Image(depth.backing->image),
        .viewType = vk::ImageViewType::e2D,
        .format = depth_format,
        .subresourceRange = {vk::ImageAspectFlagBits::eDepth, 0, 1, 0, 1},
    }));
    const auto color_view = Check(device.createImageView({
        .image = vk::Image(color.backing->image),
        .viewType = vk::ImageViewType::e2D,
        .format = vk::Format::eR8G8B8A8Unorm,
        .subresourceRange = {vk::ImageAspectFlagBits::eColor, 0, 1, 0, 1},
    }));

    scheduler.EndRendering();
    runtime.Transit(&depth, vk::ImageLayout::eGeneral, vk::PipelineStageFlagBits2::eComputeShader,
                    vk::AccessFlagBits2::eShaderRead);
    runtime.Transit(&color, vk::ImageLayout::eGeneral, vk::PipelineStageFlagBits2::eComputeShader,
                    vk::AccessFlagBits2::eShaderRead | vk::AccessFlagBits2::eShaderWrite);
    runtime.FlushBarriers();

    const PushConstants push{
        .reproject = Multiply(previous.view, current.inv_view),
        .proj = current.proj,
        .prev_proj = previous.proj,
        .size = {float(color.info.size.width), float(color.info.size.height)},
        .jitter = jitter,
        .previous_jitter = previous_jitter,
        .mode = BbToggle::Disabled(1u << 20)   ? 1u
                : BbToggle::Disabled(1u << 21) ? 2u
                : BbToggle::Disabled(1u << 22) ? 3u
                : BbToggle::Disabled(1u << 23) ? 4u
                                               : 0u,
    };
    static u32 log_counter = 0;
    if (++log_counter % 200 == 0) {
        const auto& m = push.reproject;
        std::printf("Camera motion: proj %g %g %g %g prev %g %g %g %g\n"
                    "  view  %8.4f %8.4f %8.4f %9.3f | %8.4f %8.4f %8.4f %9.3f | %8.4f %8.4f %8.4f %9.3f\n"
                    "  reproj %8.4f %8.4f %8.4f %9.4f | %8.4f %8.4f %8.4f %9.4f | %8.4f %8.4f %8.4f %9.4f\n"
                    "  depth %s %ux%u, frame %ux%u\n",
                    push.proj[0], push.proj[1], push.proj[2], push.proj[3], push.prev_proj[0],
                    push.prev_proj[1], push.prev_proj[2], push.prev_proj[3], current.view[0],
                    current.view[1], current.view[2], current.view[3], current.view[4],
                    current.view[5], current.view[6], current.view[7], current.view[8],
                    current.view[9], current.view[10], current.view[11], m[0], m[1], m[2], m[3],
                    m[4], m[5], m[6], m[7], m[8], m[9], m[10], m[11],
                    vk::to_string(depth_format).c_str(), depth.info.size.width,
                    depth.info.size.height, color.info.size.width, color.info.size.height);
    }
    const vk::DescriptorImageInfo depth_info{.imageView = depth_view,
                                             .imageLayout = vk::ImageLayout::eGeneral};
    const vk::DescriptorImageInfo color_info{.imageView = color_view,
                                             .imageLayout = vk::ImageLayout::eGeneral};
    const auto& prev_frame = *frames[frame_index ^ 1];
    const auto& next_frame = *frames[frame_index];
    frame_index ^= 1;
    const vk::DescriptorBufferInfo prev_info{prev_frame.Handle(), 0, prev_frame.SizeBytes()};
    const vk::DescriptorBufferInfo next_info{next_frame.Handle(), 0, next_frame.SizeBytes()};
    const std::array<vk::WriteDescriptorSet, 4> writes = {{
        {.dstBinding = 0,
         .descriptorCount = 1,
         .descriptorType = vk::DescriptorType::eSampledImage,
         .pImageInfo = &depth_info},
        {.dstBinding = 1,
         .descriptorCount = 1,
         .descriptorType = vk::DescriptorType::eStorageImage,
         .pImageInfo = &color_info},
        {.dstBinding = 2,
         .descriptorCount = 1,
         .descriptorType = vk::DescriptorType::eStorageBuffer,
         .pBufferInfo = &prev_info},
        {.dstBinding = 3,
         .descriptorCount = 1,
         .descriptorType = vk::DescriptorType::eStorageBuffer,
         .pBufferInfo = &next_info},
    }};
    const auto cmdbuf = scheduler.CommandBuffer();
    cmdbuf.bindPipeline(vk::PipelineBindPoint::eCompute, *overlay_pipeline);
    cmdbuf.pushDescriptorSetKHR(vk::PipelineBindPoint::eCompute, *pipeline_layout, 0, writes);
    cmdbuf.pushConstants(*pipeline_layout, vk::ShaderStageFlagBits::eCompute, 0, sizeof(push),
                         &push);
    // The previous frame buffer was written by the last dispatch.
    const vk::MemoryBarrier2 frame_barrier{
        .srcStageMask = vk::PipelineStageFlagBits2::eComputeShader,
        .srcAccessMask = vk::AccessFlagBits2::eShaderWrite,
        .dstStageMask = vk::PipelineStageFlagBits2::eComputeShader,
        .dstAccessMask = vk::AccessFlagBits2::eShaderRead | vk::AccessFlagBits2::eShaderWrite,
    };
    cmdbuf.pipelineBarrier2({.memoryBarrierCount = 1, .pMemoryBarriers = &frame_barrier});
    cmdbuf.dispatch((color.info.size.width + 7) / 8, (color.info.size.height + 7) / 8, 1);

    scheduler.DeferOperation([device, depth_view, color_view] {
        device.destroyImageView(depth_view);
        device.destroyImageView(color_view);
    });
}

} // namespace Vulkan
