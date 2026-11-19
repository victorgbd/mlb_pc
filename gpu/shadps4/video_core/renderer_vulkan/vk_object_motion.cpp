// SPDX-License-Identifier: GPL-2.0-or-later
#include "video_core/renderer_vulkan/vk_object_motion.h"

#include <algorithm>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include <vk_mem_alloc.h>
#include "bbport_settings.h"
#include "bbport_toggles.h"
#include "shader_recompiler/runtime_info.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"
#include "video_core/renderer_vulkan/vk_shader_util.h"
#include "video_core/host_shaders/motion_resolve_comp.h"

namespace Vulkan {

namespace {

bool CreateBuffer(const Instance& instance, vk::DeviceSize size, bool host, vk::Buffer& buffer,
                  VmaAllocation& allocation, void** mapped, u64& address) {
    const VkBufferCreateInfo buffer_ci{
        .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size = size,
        .usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT,
    };
    VmaAllocationCreateInfo alloc_ci{
        .usage = VMA_MEMORY_USAGE_AUTO,
    };
    if (host) {
        alloc_ci.requiredFlags = VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
        alloc_ci.flags = VMA_ALLOCATION_CREATE_MAPPED_BIT |
                         VMA_ALLOCATION_CREATE_HOST_ACCESS_SEQUENTIAL_WRITE_BIT;
    } else {
        alloc_ci.usage = VMA_MEMORY_USAGE_AUTO_PREFER_DEVICE;
    }
    VkBuffer raw{};
    VmaAllocationInfo info{};
    if (vmaCreateBuffer(instance.GetAllocator(), &buffer_ci, &alloc_ci, &raw, &allocation, &info) !=
        VK_SUCCESS) {
        return false;
    }
    buffer = raw;
    if (mapped) {
        *mapped = info.pMappedData;
    }
    address = instance.GetDevice().getBufferAddress({.buffer = buffer});
    return address != 0;
}

} // namespace

ObjectMotion::ObjectMotion(const Instance& instance_, Scheduler& scheduler_)
    : instance{instance_}, scheduler{scheduler_} {
    // Pipeline selection limits the extra attachment and vertex stores to likely
    // animated draws. A setting or environment override can still disable the path.
    if (!BbSettings::Get().object_motion ||
        BbSettings::Get().upscaler == BbSettings::UpscalerOff) {
        std::puts("Object motion: off (enable in menu or BB_OBJECT_MOTION=1)");
        return;
    }
    const auto& features = instance.GetPhysicalDevice().getFeatures();
    if (!features.vertexPipelineStoresAndAtomics) {
        std::printf("Object motion: vertexPipelineStoresAndAtomics unsupported, off\n");
        return;
    }
    void* mapped = nullptr;
    u64 params_address = 0, positions_address = 0;
    if (!CreateBuffer(instance, vk::DeviceSize(1 + FrameSlots * ParamsPerFrame) * 32, true,
                      params_buffer, params_allocation, &mapped, params_address) ||
        !CreateBuffer(instance, vk::DeviceSize(1 + 2 * PositionsPerFrame) * 16, false,
                      positions_buffer, positions_allocation, nullptr, positions_address)) {
        std::printf("Object motion: buffer creation failed, off\n");
        return;
    }
    params_mapped = static_cast<u32*>(mapped);
    std::memset(params_mapped, 0, 32);
    // MLB (BB_MLB_TEMPORAL): skinned meshes get new normal/tangent streams every frame.
    if (const char* mlb = std::getenv("BB_MLB_TEMPORAL"); mlb && mlb[0] == '1') {
        history.loose_geometry = true;
    }
    Shader::MotionVectors::params_address = params_address;
    Shader::MotionVectors::positions_address = positions_address;
    enabled = true;
    std::printf("Object motion: on (%u vertices per frame)\n", PositionsPerFrame);
}

ObjectMotion::~ObjectMotion() {
    scheduler.Finish();
    const auto allocator = instance.GetAllocator();
    if (params_buffer) {
        vmaDestroyBuffer(allocator, params_buffer, params_allocation);
    }
    if (positions_buffer) {
        vmaDestroyBuffer(allocator, positions_buffer, positions_allocation);
    }
}

void ObjectMotion::OnFrameStart() {
    if (!enabled) {
        return;
    }
    if (BbStats::enabled && (frame % 600) == 0 && history.stats.draws) {
        const auto& s = history.stats;
        std::printf("Object motion: %llu draws, %llu stored, %llu with history, %llu unmatched, "
                    "%llu capacity skips, %llu invalid, %llu still, %llu by index list (600 frames); last frame %u/%u vertices; "
                    "index ranges %llu reused, %llu scanned (%llu found changed), %zu cached\n",
                    (unsigned long long)s.draws, (unsigned long long)s.stored,
                    (unsigned long long)s.loaded, (unsigned long long)s.unmatched,
                    (unsigned long long)s.exhausted, (unsigned long long)s.invalid,
                    (unsigned long long)s.still, (unsigned long long)s.loose,
                    history.Used(), PositionsPerFrame, (unsigned long long)index_ranges.stats.hits,
                    (unsigned long long)index_ranges.stats.scans,
                    (unsigned long long)index_ranges.stats.stale, index_ranges.Size());
        index_ranges.stats = {};
        history.stats = {};
    }
    history.NextFrame();
    ++frame;
    if (frame % Motion::IndexRangeCache::Unused == 0) {
        index_ranges.Trim(frame);
    }
    const u32 slot = u32(frame % FrameSlots);
    // Do not overwrite host parameters still referenced by queued GPU work.
    if (params_ticks[slot]) {
        scheduler.Wait(params_ticks[slot]);
        params_ticks[slot] = 0;
    }
    params_used = 0;
    written = false;
    scheduler.EndRendering();
    // Last frame's stores must be visible to this frame's loads. Also finish old reads
    // before the ping-pong half they reference is reused for writes.
    scheduler.Record([](vk::CommandBuffer cmd) {
        const vk::MemoryBarrier2 barrier{
            .srcStageMask = vk::PipelineStageFlagBits2::eVertexShader,
            .srcAccessMask = vk::AccessFlagBits2::eShaderRead | vk::AccessFlagBits2::eShaderWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eVertexShader,
            .dstAccessMask = vk::AccessFlagBits2::eShaderRead | vk::AccessFlagBits2::eShaderWrite,
        };
        cmd.pipelineBarrier2({.memoryBarrierCount = 1, .pMemoryBarriers = &barrier});
    });
}

u32 ObjectMotion::PrepareDraw(const DrawInfo& draw) {
    if (!enabled || BbToggle::Disabled(1u << 29) || params_used >= ParamsPerFrame) {
        return 0;
    }
    const auto allocation = history.Prepare(draw);
    // BB_MOTION_UNMATCHED_LOG=1: the draws without history in two consecutive frames every
    // 1200, to see which part of their identity changes between frames.
    static const bool unmatched_log = [] {
        const char* env = std::getenv("BB_MOTION_UNMATCHED_LOG");
        return env && env[0] == '1';
    }();
    if (unmatched_log && frame % 1200 < 2) {
        std::printf("Motion %s f%llu: vs %08x geo %016llx stable %016llx idx %#llx topo %016llx "
                    "n %u inst %u/%u range %d+%u\n",
                    allocation.load ? "match" : "UNMATCHED", (unsigned long long)frame,
                    u32(draw.shader), (unsigned long long)draw.geometry,
                    (unsigned long long)draw.stable, (unsigned long long)draw.indices,
                    (unsigned long long)draw.topology, draw.index_count, draw.instances,
                    draw.first_instance, draw.vertices.first, draw.vertices.count);
    }
    u32 flags = (allocation.store ? Shader::MotionVectors::FlagStore : 0) |
                (allocation.load ? Shader::MotionVectors::FlagLoad : 0);
    if (!flags) {
        return 0;
    }
    const u32 slot = u32(frame % FrameSlots);
    const u32 index = 1 + slot * ParamsPerFrame + params_used++;
    u32* param = params_mapped + index * 8;
    param[0] = allocation.store;
    param[1] = allocation.load;
    param[2] = allocation.vertices;
    param[3] = flags;
    param[4] = allocation.first_vertex;
    param[5] = allocation.first_instance;
    param[6] = allocation.instances;
    param[7] = 0;
    params_ticks[slot] = scheduler.CurrentTick();
    return index;
}

void ObjectMotion::CreateResolvePipeline() {
    if (resolve_pipeline) {
        return;
    }
    const auto device = instance.GetDevice();
    const std::array<vk::DescriptorSetLayoutBinding, 2> bindings = {{
        {.binding = 0,
         .descriptorType = vk::DescriptorType::eSampledImage,
         .descriptorCount = 1,
         .stageFlags = vk::ShaderStageFlagBits::eCompute},
        {.binding = 1,
         .descriptorType = vk::DescriptorType::eStorageImage,
         .descriptorCount = 1,
         .stageFlags = vk::ShaderStageFlagBits::eCompute},
    }};
    resolve_desc_layout = Check(device.createDescriptorSetLayoutUnique({
        .flags = vk::DescriptorSetLayoutCreateFlagBits::ePushDescriptorKHR,
        .bindingCount = u32(bindings.size()),
        .pBindings = bindings.data(),
    }));
    resolve_pipeline_layout = Check(device.createPipelineLayoutUnique({
        .setLayoutCount = 1,
        .pSetLayouts = &*resolve_desc_layout,
    }));
    const auto module = CompileSPV(MOTION_RESOLVE_COMP, device);
    resolve_pipeline = Check(device.createComputePipelineUnique(
        {}, vk::ComputePipelineCreateInfo{
                .stage = {.stage = vk::ShaderStageFlagBits::eCompute,
                          .module = module,
                          .pName = "main"},
                .layout = *resolve_pipeline_layout,
            }));
    device.destroyShaderModule(module);
}

void ObjectMotion::EnsureImage(u32 width, u32 height, u32 samples) {
    if (image && width == image_width && height == image_height && samples == image_samples) {
        return;
    }
    const auto device = instance.GetDevice();
    if (image) {
        scheduler.Finish();
    }
    view.reset();
    resolved_view.reset();
    resolved_image = VideoCore::UniqueImage{};
    image_layout = vk::ImageLayout::eUndefined;
    image = VideoCore::UniqueImage(device, instance.GetAllocator());
    image.Create(vk::ImageCreateInfo{
        .imageType = vk::ImageType::e2D,
        .format = vk::Format::eR32G32B32A32Sfloat,
        .extent = {width, height, 1},
        .mipLevels = 1,
        .arrayLayers = 1,
        .samples = vk::SampleCountFlagBits(samples),
        .tiling = vk::ImageTiling::eOptimal,
        .usage = vk::ImageUsageFlagBits::eColorAttachment | vk::ImageUsageFlagBits::eSampled |
                 (samples == 1 ? vk::ImageUsageFlagBits::eTransferSrc : vk::ImageUsageFlags{}),
        .initialLayout = vk::ImageLayout::eUndefined,
    });
    view = Check(device.createImageViewUnique({
        .image = vk::Image(image),
        .viewType = vk::ImageViewType::e2D,
        .format = vk::Format::eR32G32B32A32Sfloat,
        .subresourceRange = {vk::ImageAspectFlagBits::eColor, 0, 1, 0, 1},
    }));
    if (samples > 1) {
        // MLB's 4x MSAA scene: the compose pass reads sample 0 (as for the scene depth).
        CreateResolvePipeline();
        resolved_image = VideoCore::UniqueImage(device, instance.GetAllocator());
        resolved_image.Create(vk::ImageCreateInfo{
            .imageType = vk::ImageType::e2D,
            .format = vk::Format::eR32G32B32A32Sfloat,
            .extent = {width, height, 1},
            .mipLevels = 1,
            .arrayLayers = 1,
            .samples = vk::SampleCountFlagBits::e1,
            .tiling = vk::ImageTiling::eOptimal,
            .usage = vk::ImageUsageFlagBits::eStorage | vk::ImageUsageFlagBits::eSampled |
                     vk::ImageUsageFlagBits::eTransferSrc,
            .initialLayout = vk::ImageLayout::eUndefined,
        });
        resolved_view = Check(device.createImageViewUnique({
            .image = vk::Image(resolved_image),
            .viewType = vk::ImageViewType::e2D,
            .format = vk::Format::eR32G32B32A32Sfloat,
            .subresourceRange = {vk::ImageAspectFlagBits::eColor, 0, 1, 0, 1},
        }));
        std::printf("Object motion: %ux%u attachment with %u samples\n", width, height, samples);
    }
    image_width = width;
    image_height = height;
    image_samples = samples;
    written = false;
}

void ObjectMotion::Attach(RenderState& state, u32 width, u32 height, u32 samples) {
    constexpr u32 slot = Shader::MotionVectors::Output;
    EnsureImage(width, height, std::max(samples, 1u));
    auto& attachment = state.color_attachments[slot];
    attachment = {};
    attachment.image_view = *view;
    attachment.image_layout = vk::ImageLayout::eColorAttachmentOptimal;
    if (!written || image_layout != vk::ImageLayout::eColorAttachmentOptimal) {
        // Clear once per frame; later reads must not erase the validity of this history.
        scheduler.EndRendering();
        const vk::ImageMemoryBarrier2 barrier{
            .srcStageMask = vk::PipelineStageFlagBits2::eAllCommands,
            .srcAccessMask = vk::AccessFlagBits2::eMemoryRead | vk::AccessFlagBits2::eMemoryWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eColorAttachmentOutput,
            .dstAccessMask = vk::AccessFlagBits2::eColorAttachmentWrite |
                             vk::AccessFlagBits2::eColorAttachmentRead,
            .oldLayout = image_layout,
            .newLayout = vk::ImageLayout::eColorAttachmentOptimal,
            .image = vk::Image(image),
            .subresourceRange = {vk::ImageAspectFlagBits::eColor, 0, 1, 0, 1},
        };
        // Recorded in order on the recording thread: CommandBuffer() here switched the rest of
        // the submission (most of the G-buffer) to direct recording on the GPU thread.
        scheduler.Record([barrier](vk::CommandBuffer cmd) {
            cmd.pipelineBarrier2({.imageMemoryBarrierCount = 1, .pImageMemoryBarriers = &barrier});
        });
        image_layout = vk::ImageLayout::eColorAttachmentOptimal;
        attachment.is_clear = !written;
        written = true;
    }
    state.num_color_attachments = std::max<u16>(state.num_color_attachments, slot + 1);
}

vk::ImageView ObjectMotion::PrepareRead(vk::CommandBuffer cmdbuf, u32 width, u32 height,
                                        bool& valid) {
    EnsureImage(std::max(image_width, 1u), std::max(image_height, 1u), image_samples);
    valid = written && width == image_width && height == image_height;
    const vk::ImageMemoryBarrier2 barrier{
        .srcStageMask = vk::PipelineStageFlagBits2::eAllCommands,
        .srcAccessMask = vk::AccessFlagBits2::eMemoryRead | vk::AccessFlagBits2::eMemoryWrite,
        .dstStageMask = vk::PipelineStageFlagBits2::eComputeShader,
        .dstAccessMask = vk::AccessFlagBits2::eShaderRead,
        .oldLayout = image_layout,
        .newLayout = vk::ImageLayout::eGeneral,
        .image = vk::Image(image),
        .subresourceRange = {vk::ImageAspectFlagBits::eColor, 0, 1, 0, 1},
    };
    cmdbuf.pipelineBarrier2({.imageMemoryBarrierCount = 1, .pImageMemoryBarriers = &barrier});
    image_layout = vk::ImageLayout::eGeneral;
    if (image_samples == 1) {
        return *view;
    }
    // Sample 0 of each pixel into the one-sample image.
    const auto resolved_barrier = [&](vk::ImageLayout old_layout, vk::PipelineStageFlags2 src,
                                      vk::AccessFlags2 src_access, vk::AccessFlags2 dst_access) {
        const vk::ImageMemoryBarrier2 b{
            .srcStageMask = src,
            .srcAccessMask = src_access,
            .dstStageMask = vk::PipelineStageFlagBits2::eComputeShader,
            .dstAccessMask = dst_access,
            .oldLayout = old_layout,
            .newLayout = vk::ImageLayout::eGeneral,
            .image = vk::Image(resolved_image),
            .subresourceRange = {vk::ImageAspectFlagBits::eColor, 0, 1, 0, 1},
        };
        cmdbuf.pipelineBarrier2({.imageMemoryBarrierCount = 1, .pImageMemoryBarriers = &b});
    };
    resolved_barrier(vk::ImageLayout::eUndefined, vk::PipelineStageFlagBits2::eAllCommands,
                     vk::AccessFlagBits2::eNone, vk::AccessFlagBits2::eShaderWrite);
    const vk::DescriptorImageInfo source_info{.imageView = *view,
                                              .imageLayout = vk::ImageLayout::eGeneral};
    const vk::DescriptorImageInfo out_info{.imageView = *resolved_view,
                                           .imageLayout = vk::ImageLayout::eGeneral};
    const std::array<vk::WriteDescriptorSet, 2> writes = {{
        {.dstBinding = 0,
         .descriptorCount = 1,
         .descriptorType = vk::DescriptorType::eSampledImage,
         .pImageInfo = &source_info},
        {.dstBinding = 1,
         .descriptorCount = 1,
         .descriptorType = vk::DescriptorType::eStorageImage,
         .pImageInfo = &out_info},
    }};
    cmdbuf.bindPipeline(vk::PipelineBindPoint::eCompute, *resolve_pipeline);
    cmdbuf.pushDescriptorSetKHR(vk::PipelineBindPoint::eCompute, *resolve_pipeline_layout, 0,
                                writes);
    cmdbuf.dispatch((image_width + 7) / 8, (image_height + 7) / 8, 1);
    resolved_barrier(vk::ImageLayout::eGeneral, vk::PipelineStageFlagBits2::eComputeShader,
                     vk::AccessFlagBits2::eShaderWrite, vk::AccessFlagBits2::eShaderRead);
    return *resolved_view;
}

} // namespace Vulkan
