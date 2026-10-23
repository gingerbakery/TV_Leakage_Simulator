#include "probe_shared.h"
#include <optix_function_table_definition.h>
#include <optix_stubs.h>
#include <cuda.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <fstream>
#include <iterator>
#include <limits>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
thread_local std::string last_error;
std::once_flag initialization;
#ifdef PERF5E_TEST_FAULTS
thread_local bool fail_after_device_launch = false;
thread_local int pending_before_injected_failure = 0;
#endif

void cuda_check(CUresult result) {
    if (result == CUDA_SUCCESS) return;
    const char* detail = nullptr;
    cuGetErrorString(result, &detail);
    throw std::runtime_error(detail ? detail : "CUDA driver call failed");
}

void optix_check(OptixResult result, const char* log = "") {
    if (result != OPTIX_SUCCESS) {
        throw std::runtime_error(std::string(optixGetErrorName(result)) + ": " + log);
    }
}

struct CurrentContext {
    explicit CurrentContext(CUcontext context) { cuda_check(cuCtxPushCurrent(context)); }
    ~CurrentContext() { CUcontext previous; cuCtxPopCurrent(&previous); }
};

struct Buffer {
    CUdeviceptr pointer = 0;
    size_t capacity = 0;
    ~Buffer() { if (pointer) cuMemFree(pointer); }
    void reserve(size_t bytes) {
        if (bytes <= capacity) return;
        CUdeviceptr replacement = 0;
        cuda_check(cuMemAlloc(&replacement, bytes));
        if (pointer) cuMemFree(pointer);
        pointer = replacement;
        capacity = bytes;
    }
    void upload(const void* source, size_t bytes) {
        reserve(bytes);
        if (bytes) {
            cuda_check(cuMemcpyHtoD(pointer, source, bytes));
            cuda_check(cuStreamSynchronize(nullptr));
        }
    }
    void release() {
        if (pointer) cuMemFree(pointer);
        pointer = 0;
        capacity = 0;
    }
};

struct alignas(OPTIX_SBT_RECORD_ALIGNMENT) Record {
    char header[OPTIX_SBT_RECORD_HEADER_SIZE];
};

struct StreamCompletion {
    CUstream stream;
    bool pending = true;
    ~StreamCompletion() { if (pending) cuStreamSynchronize(stream); }
};

void validate_stream(CUstream stream, CUcontext expected) {
    CUcontext actual = nullptr;
    cuda_check(cuStreamGetCtx(stream, &actual));
    if (actual != expected) throw std::runtime_error("Stream belongs to another CUDA context");
    CUstreamCaptureStatus capture;
    cuda_check(cuStreamIsCapturing(stream, &capture));
    if (capture != CU_STREAM_CAPTURE_STATUS_NONE) throw std::runtime_error("Captured streams are unsupported by the synchronous probe");
}

void validate_device_buffer(CUdeviceptr pointer, size_t bytes, CUcontext expected) {
    if (!pointer || pointer % alignof(double)) throw std::runtime_error("Device buffer must be nonnull and aligned");
    CUcontext actual = nullptr;
    CUmemorytype memory_type;
    cuda_check(cuPointerGetAttribute(&actual, CU_POINTER_ATTRIBUTE_CONTEXT, pointer));
    cuda_check(cuPointerGetAttribute(&memory_type, CU_POINTER_ATTRIBUTE_MEMORY_TYPE, pointer));
    if (actual != expected || memory_type != CU_MEMORYTYPE_DEVICE) throw std::runtime_error("Buffer must be device memory in the probe CUDA context");
    CUdeviceptr base = 0;
    size_t capacity = 0;
    cuda_check(cuMemGetAddressRange(&base, &capacity, pointer));
    if (pointer < base || pointer - base > capacity || bytes > capacity - (pointer - base)) {
        throw std::runtime_error("Device buffer exceeds its allocation");
    }
}

void wait_for_producer(CUstream producer, CUstream consumer) {
    if (!producer || producer == consumer) return;
    CUevent ready = nullptr;
    cuda_check(cuEventCreate(&ready, CU_EVENT_DISABLE_TIMING));
    try {
        cuda_check(cuEventRecord(ready, producer));
        cuda_check(cuStreamWaitEvent(consumer, ready, 0));
    } catch (...) {
        cuEventDestroy(ready);
        throw;
    }
    cuda_check(cuEventDestroy(ready));
}

struct Scene {
    CUdevice device = 0;
    CUcontext cuda_context = nullptr;
    CUstream stream = nullptr;
    OptixDeviceContext context = nullptr;
    OptixModule module = nullptr;
    OptixPipeline pipeline = nullptr;
    std::array<OptixProgramGroup, 3> groups{};
    OptixShaderBindingTable table{};
    ProbeParams parameters{};
    ProbeParams* pinned_parameters = nullptr;
    Buffer vertices, edges1, edges2, ids, acceleration, records, rays, hits, launch, expected, visited;
    std::mutex query_lock;
    ~Scene() {
        if (!cuda_context) return;
        if (cuCtxPushCurrent(cuda_context) != CUDA_SUCCESS) return;
        if (stream) cuStreamSynchronize(stream);
        if (pipeline) optixPipelineDestroy(pipeline);
        for (auto group : groups) if (group) optixProgramGroupDestroy(group);
        if (module) optixModuleDestroy(module);
        vertices.release(); edges1.release(); edges2.release(); ids.release();
        acceleration.release(); records.release(); rays.release(); hits.release(); launch.release();
        expected.release(); visited.release();
        if (pinned_parameters) cuMemFreeHost(pinned_parameters);
        if (context) optixDeviceContextDestroy(context);
        if (stream) cuStreamDestroy(stream);
        CUcontext previous;
        cuCtxPopCurrent(&previous);
        cuDevicePrimaryCtxRelease(device);
    }
};

void make_pipeline(Scene& scene, const char* ptx_path) {
    std::ifstream input(ptx_path, std::ios::binary);
    if (!input) throw std::runtime_error("Cannot read probe PTX");
    const std::string ptx((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    OptixModuleCompileOptions module_options{};
    OptixPipelineCompileOptions options{};
    options.traversableGraphFlags = OPTIX_TRAVERSABLE_GRAPH_FLAG_ALLOW_SINGLE_GAS;
    options.numPayloadValues = 1;
    options.numAttributeValues = 2;
    options.pipelineLaunchParamsVariableName = "params";
    options.usesPrimitiveTypeFlags = OPTIX_PRIMITIVE_TYPE_FLAGS_CUSTOM;
    char log[8192]{};
    size_t log_size = sizeof(log);
    auto result = optixModuleCreate(scene.context, &module_options, &options, ptx.data(), ptx.size(), log, &log_size, &scene.module);
    optix_check(result, log);
    std::array<OptixProgramGroupDesc, 3> descriptors{};
    descriptors[0].kind = OPTIX_PROGRAM_GROUP_KIND_RAYGEN;
    descriptors[0].raygen = {scene.module, "__raygen__probe"};
    descriptors[1].kind = OPTIX_PROGRAM_GROUP_KIND_MISS;
    descriptors[1].miss = {scene.module, "__miss__probe"};
    descriptors[2].kind = OPTIX_PROGRAM_GROUP_KIND_HITGROUP;
    descriptors[2].hitgroup.moduleIS = scene.module;
    descriptors[2].hitgroup.entryFunctionNameIS = "__intersection__probe";
    OptixProgramGroupOptions group_options{};
    log_size = sizeof(log);
    result = optixProgramGroupCreate(scene.context, descriptors.data(), 3, &group_options, log, &log_size, scene.groups.data());
    optix_check(result, log);
    OptixPipelineLinkOptions link_options{};
    link_options.maxTraceDepth = 1;
    log_size = sizeof(log);
    result = optixPipelineCreate(scene.context, &options, &link_options, scene.groups.data(), 3, log, &log_size, &scene.pipeline);
    optix_check(result, log);
    optix_check(optixPipelineSetStackSizeFromCallDepths(scene.pipeline, 1, 0, 0, 0, 1));
    std::array<Record, 3> records{};
    for (size_t index = 0; index < records.size(); ++index) optix_check(optixSbtRecordPackHeader(scene.groups[index], &records[index]));
    scene.records.upload(records.data(), sizeof(records));
    scene.table.raygenRecord = scene.records.pointer;
    scene.table.missRecordBase = scene.records.pointer + sizeof(Record);
    scene.table.missRecordStrideInBytes = sizeof(Record);
    scene.table.missRecordCount = 1;
    scene.table.hitgroupRecordBase = scene.records.pointer + 2 * sizeof(Record);
    scene.table.hitgroupRecordStrideInBytes = sizeof(Record);
    scene.table.hitgroupRecordCount = 1;
}

void make_geometry(Scene& scene, const double* vertices, const double* edges1, const double* edges2, const int64_t* ids, uint32_t count) {
    std::array<double, 3> minimum{INFINITY, INFINITY, INFINITY};
    std::array<double, 3> maximum{-INFINITY, -INFINITY, -INFINITY};
    for (size_t index = 0; index < static_cast<size_t>(count) * 3; ++index) {
        const double coordinates[] = {vertices[index], vertices[index] + edges1[index], vertices[index] + edges2[index]};
        for (double value : coordinates) {
            if (!std::isfinite(value) || std::abs(value) > 1e12) throw std::runtime_error("Geometry exceeds the experimental numeric domain");
            minimum[index % 3] = std::min(minimum[index % 3], value);
            maximum[index % 3] = std::max(maximum[index % 3], value);
        }
    }
    double diagonal_squared = 0.0;
    for (size_t axis = 0; axis < 3; ++axis) {
        scene.parameters.center[axis] = 0.5 * (minimum[axis] + maximum[axis]);
        diagonal_squared += std::pow(maximum[axis] - minimum[axis], 2);
    }
    scene.parameters.radius = 0.5 * std::sqrt(diagonal_squared);
    std::vector<std::array<double, 6>> local_bounds(count);
    std::array<double, 3> radial_extents{};
    for (size_t primitive = 0; primitive < count; ++primitive) {
        for (size_t axis = 0; axis < 3; ++axis) {
            const size_t index = primitive * 3 + axis;
            const double base = vertices[index] - scene.parameters.center[axis];
            const double base_low = std::nextafter(base, -INFINITY);
            const double base_high = std::nextafter(base, INFINITY);
            const double low = std::min({base_low, std::nextafter(base_low + edges1[index], -INFINITY),
                std::nextafter(base_low + edges2[index], -INFINITY)});
            const double high = std::max({base_high, std::nextafter(base_high + edges1[index], INFINITY),
                std::nextafter(base_high + edges2[index], INFINITY)});
            local_bounds[primitive][axis] = low;
            local_bounds[primitive][axis + 3] = high;
            radial_extents[axis] = std::max({radial_extents[axis], std::abs(low), std::abs(high)});
        }
    }
    double radial_squared = 0.0;
    for (double extent : radial_extents) {
        radial_squared = std::nextafter(radial_squared + std::nextafter(extent * extent, INFINITY), INFINITY);
    }
    scene.parameters.radius = std::max(scene.parameters.radius, std::nextafter(std::sqrt(radial_squared), INFINITY));
    scene.parameters.padding = std::max(1e-5, 2e-5 * scene.parameters.radius);
    std::vector<OptixAabb> boxes(count);
    for (size_t primitive = 0; primitive < count; ++primitive) {
        if (ids[primitive] < 0) throw std::runtime_error("Face IDs must be nonnegative");
        float lower[3], upper[3];
        for (size_t axis = 0; axis < 3; ++axis) {
            const double low = std::nextafter(local_bounds[primitive][axis] - scene.parameters.padding, -INFINITY);
            const double high = std::nextafter(local_bounds[primitive][axis + 3] + scene.parameters.padding, INFINITY);
            lower[axis] = std::nextafter(static_cast<float>(low), -INFINITY);
            upper[axis] = std::nextafter(static_cast<float>(high), INFINITY);
        }
        boxes[primitive] = {lower[0], lower[1], lower[2], upper[0], upper[1], upper[2]};
    }
    scene.vertices.upload(vertices, count * 3ULL * sizeof(double));
    scene.edges1.upload(edges1, count * 3ULL * sizeof(double));
    scene.edges2.upload(edges2, count * 3ULL * sizeof(double));
    scene.ids.upload(ids, count * sizeof(int64_t));
    scene.parameters.vertices = reinterpret_cast<const double*>(scene.vertices.pointer);
    scene.parameters.edges1 = reinterpret_cast<const double*>(scene.edges1.pointer);
    scene.parameters.edges2 = reinterpret_cast<const double*>(scene.edges2.pointer);
    scene.parameters.face_ids = reinterpret_cast<const int64_t*>(scene.ids.pointer);
    Buffer box_buffer, temporary;
    box_buffer.upload(boxes.data(), boxes.size() * sizeof(OptixAabb));
    OptixBuildInput build{};
    uint32_t flags = OPTIX_GEOMETRY_FLAG_NONE;
    build.type = OPTIX_BUILD_INPUT_TYPE_CUSTOM_PRIMITIVES;
    build.customPrimitiveArray.aabbBuffers = &box_buffer.pointer;
    build.customPrimitiveArray.numPrimitives = count;
    build.customPrimitiveArray.flags = &flags;
    build.customPrimitiveArray.numSbtRecords = 1;
    OptixAccelBuildOptions options{};
    options.buildFlags = OPTIX_BUILD_FLAG_PREFER_FAST_TRACE;
    options.operation = OPTIX_BUILD_OPERATION_BUILD;
    OptixAccelBufferSizes sizes{};
    optix_check(optixAccelComputeMemoryUsage(scene.context, &options, &build, 1, &sizes));
    temporary.reserve(sizes.tempSizeInBytes);
    scene.acceleration.reserve(sizes.outputSizeInBytes);
    optix_check(optixAccelBuild(scene.context, scene.stream, &options, &build, 1, temporary.pointer, sizes.tempSizeInBytes,
        scene.acceleration.pointer, sizes.outputSizeInBytes, &scene.parameters.traversable, nullptr, 0));
    cuda_check(cuStreamSynchronize(scene.stream));
}
}

extern "C" __declspec(dllexport) const char* probe_error() { return last_error.c_str(); }
extern "C" __declspec(dllexport) int probe_abi() { return 2; }
extern "C" __declspec(dllexport) int probe_coverage_abi() { return 1; }

#ifdef PERF5E_TEST_FAULTS
extern "C" __declspec(dllexport) void probe_test_fail_after_launch(int enabled) {
    fail_after_device_launch = enabled != 0;
}
extern "C" __declspec(dllexport) int probe_test_pending_before_failure() {
    return pending_before_injected_failure;
}
extern "C" __declspec(dllexport) int probe_test_stream_status(void* handle) {
    auto& scene = *static_cast<Scene*>(handle);
    CurrentContext current(scene.cuda_context);
    return static_cast<int>(cuStreamQuery(scene.stream));
}
#endif

extern "C" __declspec(dllexport) int probe_info(void* handle, double* values) {
    if (!handle || !values) return -1;
    const auto& scene = *static_cast<Scene*>(handle);
    for (int axis = 0; axis < 3; ++axis) values[axis] = scene.parameters.center[axis];
    values[3] = scene.parameters.radius;
    values[4] = scene.parameters.padding;
    values[5] = static_cast<double>(scene.acceleration.capacity);
    return 0;
}

extern "C" __declspec(dllexport) void* probe_create(const double* vertices, const double* edges1, const double* edges2,
    const int64_t* ids, uint32_t count, const char* ptx_path, int device_ordinal) {
    try {
        last_error.clear();
        if (!vertices || !edges1 || !edges2 || !ids || !count || !ptx_path) throw std::runtime_error("Invalid scene input");
        std::call_once(initialization, [] { cuda_check(cuInit(0)); optix_check(optixInit()); });
        auto scene = std::make_unique<Scene>();
        cuda_check(cuDeviceGet(&scene->device, device_ordinal));
        cuda_check(cuDevicePrimaryCtxRetain(&scene->cuda_context, scene->device));
        CurrentContext current(scene->cuda_context);
        cuda_check(cuStreamCreate(&scene->stream, CU_STREAM_NON_BLOCKING));
        OptixDeviceContextOptions options{};
        optix_check(optixDeviceContextCreate(scene->cuda_context, &options, &scene->context));
        make_pipeline(*scene, ptx_path);
        make_geometry(*scene, vertices, edges1, edges2, ids, count);
        return scene.release();
    } catch (const std::exception& error) {
        last_error = error.what();
        return nullptr;
    }
}

extern "C" __declspec(dllexport) int probe_query(void* handle, const ProbeRay* rays, uint32_t count, ProbeHit* hits, double* milliseconds) {
    try {
        last_error.clear();
        if (!handle || !rays || !hits || !milliseconds || !count) throw std::runtime_error("Invalid query input");
        auto& scene = *static_cast<Scene*>(handle);
        std::lock_guard<std::mutex> lock(scene.query_lock);
        CurrentContext current(scene.cuda_context);
        const auto started = std::chrono::steady_clock::now();
        scene.rays.upload(rays, count * sizeof(ProbeRay));
        scene.hits.reserve(count * sizeof(ProbeHit));
        scene.parameters.rays = reinterpret_cast<const ProbeRay*>(scene.rays.pointer);
        scene.parameters.hits = reinterpret_cast<ProbeHit*>(scene.hits.pointer);
        scene.launch.upload(&scene.parameters, sizeof(ProbeParams));
        const auto uploaded = std::chrono::steady_clock::now();
        optix_check(optixLaunch(scene.pipeline, scene.stream, scene.launch.pointer, sizeof(ProbeParams), &scene.table, count, 1, 1));
        cuda_check(cuStreamSynchronize(scene.stream));
        const auto launched = std::chrono::steady_clock::now();
        cuda_check(cuMemcpyDtoH(hits, scene.hits.pointer, count * sizeof(ProbeHit)));
        const auto finished = std::chrono::steady_clock::now();
        milliseconds[0] = std::chrono::duration<double, std::milli>(uploaded - started).count();
        milliseconds[1] = std::chrono::duration<double, std::milli>(launched - uploaded).count();
        milliseconds[2] = std::chrono::duration<double, std::milli>(finished - launched).count();
        return 0;
    } catch (const std::exception& error) {
        last_error = error.what();
        return -1;
    }
}

extern "C" __declspec(dllexport) int probe_query_device(void* handle, CUdeviceptr rays, uint32_t count,
    CUdeviceptr hits, CUstream rays_producer, CUstream hits_producer, CUstream execution_stream, double* milliseconds) {
    try {
        last_error.clear();
        if (!handle || !milliseconds || !count || !execution_stream) throw std::runtime_error("Invalid device query input");
        auto& scene = *static_cast<Scene*>(handle);
        std::lock_guard<std::mutex> lock(scene.query_lock);
        CurrentContext current(scene.cuda_context);
        validate_stream(execution_stream, scene.cuda_context);
        if (rays_producer) validate_stream(rays_producer, scene.cuda_context);
        if (hits_producer) validate_stream(hits_producer, scene.cuda_context);
        const size_t ray_bytes = static_cast<size_t>(count) * sizeof(ProbeRay);
        const size_t hit_bytes = static_cast<size_t>(count) * sizeof(ProbeHit);
        validate_device_buffer(rays, ray_bytes, scene.cuda_context);
        validate_device_buffer(hits, hit_bytes, scene.cuda_context);
        if ((rays <= hits && hits - rays < ray_bytes) || (hits < rays && rays - hits < hit_bytes)) {
            throw std::runtime_error("Input and output device buffers overlap");
        }
        StreamCompletion completion{execution_stream};
        const auto started = std::chrono::steady_clock::now();
        wait_for_producer(rays_producer, execution_stream);
        if (hits_producer != rays_producer) wait_for_producer(hits_producer, execution_stream);
        if (!scene.pinned_parameters) {
            cuda_check(cuMemHostAlloc(reinterpret_cast<void**>(&scene.pinned_parameters), sizeof(ProbeParams), 0));
        }
        scene.parameters.rays = reinterpret_cast<const ProbeRay*>(rays);
        scene.parameters.hits = reinterpret_cast<ProbeHit*>(hits);
        *scene.pinned_parameters = scene.parameters;
        scene.launch.reserve(sizeof(ProbeParams));
        cuda_check(cuMemcpyHtoDAsync(scene.launch.pointer, scene.pinned_parameters, sizeof(ProbeParams), execution_stream));
        const auto prepared = std::chrono::steady_clock::now();
        optix_check(optixLaunch(scene.pipeline, execution_stream, scene.launch.pointer, sizeof(ProbeParams), &scene.table, count, 1, 1));
#ifdef PERF5E_TEST_FAULTS
        if (fail_after_device_launch) {
            fail_after_device_launch = false;
            pending_before_injected_failure = static_cast<int>(cuStreamQuery(execution_stream));
            throw std::runtime_error("Injected failure after device launch");
        }
#endif
        cuda_check(cuStreamSynchronize(execution_stream));
        completion.pending = false;
        const auto finished = std::chrono::steady_clock::now();
        milliseconds[0] = std::chrono::duration<double, std::milli>(prepared - started).count();
        milliseconds[1] = std::chrono::duration<double, std::milli>(finished - prepared).count();
        return 0;
    } catch (const std::exception& error) {
        last_error = error.what();
        return -1;
    }
}

extern "C" __declspec(dllexport) int probe_query_coverage(void* handle, const ProbeRay* rays, uint32_t count,
    const int64_t* expected, ProbeHit* hits, uint8_t* visited, double* milliseconds) {
    try {
        last_error.clear();
        if (!handle || !rays || !count || !expected || !hits || !visited || !milliseconds) {
            throw std::runtime_error("Invalid coverage query input");
        }
        auto& scene = *static_cast<Scene*>(handle);
        std::lock_guard<std::mutex> lock(scene.query_lock);
        CurrentContext current(scene.cuda_context);
        StreamCompletion completion{scene.stream};
        const auto started = std::chrono::steady_clock::now();
        scene.rays.upload(rays, static_cast<size_t>(count) * sizeof(ProbeRay));
        scene.expected.upload(expected, static_cast<size_t>(count) * 2 * sizeof(int64_t));
        scene.hits.reserve(static_cast<size_t>(count) * sizeof(ProbeHit));
        scene.visited.reserve(static_cast<size_t>(count) * sizeof(uint8_t));
        ProbeParams parameters = scene.parameters;
        parameters.rays = reinterpret_cast<const ProbeRay*>(scene.rays.pointer);
        parameters.hits = reinterpret_cast<ProbeHit*>(scene.hits.pointer);
        parameters.expected_faces = reinterpret_cast<const int64_t*>(scene.expected.pointer);
        parameters.visited_expected = reinterpret_cast<uint8_t*>(scene.visited.pointer);
        scene.launch.upload(&parameters, sizeof(ProbeParams));
        const auto uploaded = std::chrono::steady_clock::now();
        optix_check(optixLaunch(scene.pipeline, scene.stream, scene.launch.pointer, sizeof(ProbeParams), &scene.table, count, 1, 1));
#ifdef PERF5E_TEST_FAULTS
        if (fail_after_device_launch) {
            fail_after_device_launch = false;
            pending_before_injected_failure = static_cast<int>(cuStreamQuery(scene.stream));
            throw std::runtime_error("Injected failure after coverage launch");
        }
#endif
        cuda_check(cuStreamSynchronize(scene.stream));
        completion.pending = false;
        const auto launched = std::chrono::steady_clock::now();
        cuda_check(cuMemcpyDtoH(hits, scene.hits.pointer, static_cast<size_t>(count) * sizeof(ProbeHit)));
        cuda_check(cuMemcpyDtoH(visited, scene.visited.pointer, static_cast<size_t>(count) * sizeof(uint8_t)));
        const auto finished = std::chrono::steady_clock::now();
        milliseconds[0] = std::chrono::duration<double, std::milli>(uploaded - started).count();
        milliseconds[1] = std::chrono::duration<double, std::milli>(launched - uploaded).count();
        milliseconds[2] = std::chrono::duration<double, std::milli>(finished - launched).count();
        return 0;
    } catch (const std::exception& error) {
        last_error = error.what();
        return -1;
    }
}

extern "C" __declspec(dllexport) void probe_destroy(void* handle) { delete static_cast<Scene*>(handle); }
