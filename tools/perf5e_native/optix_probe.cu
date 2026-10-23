#include "probe_shared.h"
#include <optix_device.h>
#include <math_constants.h>
#include <cmath>
#include <limits>

extern "C" {
__constant__ ProbeParams params;
}

extern "C" __global__ void __raygen__probe() {
    const unsigned int ray_index = optixGetLaunchIndex().x;
    const ProbeRay& ray = params.rays[ray_index];
    ProbeHit& hit = params.hits[ray_index];
    hit.distance = CUDART_INF;
    hit.second_distance = CUDART_INF;
    hit.face = -1;
    hit.flags = 0;
    hit.candidates = 0;
    hit.intersections = 0;
    if (params.visited_expected) params.visited_expected[ray_index] = 0;
    double relative[3];
    double norm_squared = 0.0;
    double origin_squared = 0.0;
    for (int axis = 0; axis < 3; ++axis) {
        relative[axis] = ray.origin[axis] - params.center[axis];
        norm_squared += ray.direction[axis] * ray.direction[axis];
        origin_squared += relative[axis] * relative[axis];
        if (!isfinite(relative[axis]) || !isfinite(ray.direction[axis]) ||
            (ray.direction[axis] != 0.0 && fabs(ray.direction[axis]) < 1e-12)) {
            hit.flags |= 4;
        }
    }
    const double origin_distance = sqrt(origin_squared);
    const double reach = origin_distance + params.radius + 4.0 * params.padding;
    if (fabs(norm_squared - 1.0) > 1e-8 || !isfinite(reach) ||
        4e-7 * (origin_distance + reach + 1.0) >= params.padding ||
        !isfinite(ray.minimum) || isnan(ray.maximum) || ray.minimum < 0.0) {
        hit.flags |= 4;
    }
    if (hit.flags || ray.maximum <= ray.minimum) return;
    const float trace_maximum = nextafterf(static_cast<float>(fmin(reach, ray.maximum)), CUDART_INF_F);
    if (!isfinite(trace_maximum) || trace_maximum <= 0.0f) {
        hit.flags |= 4;
        return;
    }
    unsigned int payload = ray_index;
    optixTrace(params.traversable,
        make_float3(static_cast<float>(relative[0]), static_cast<float>(relative[1]), static_cast<float>(relative[2])),
        make_float3(static_cast<float>(ray.direction[0]), static_cast<float>(ray.direction[1]), static_cast<float>(ray.direction[2])),
        0.0f, trace_maximum, 0.0f, 255, OPTIX_RAY_FLAG_NONE, 0, 1, 0, payload);
    if (hit.face >= 0 && hit.second_distance - hit.distance <= 1e-8) hit.flags |= 1;
}

extern "C" __global__ void __miss__probe() {}

extern "C" __global__ void __intersection__probe() {
    const unsigned int ray_index = optixGetPayload_0();
    const unsigned int primitive = optixGetPrimitiveIndex();
    ProbeHit& hit = params.hits[ray_index];
    const ProbeRay& ray = params.rays[ray_index];
    ++hit.candidates;
    const int64_t face = params.face_ids[primitive];
    if (params.visited_expected) {
        if (face == params.expected_faces[2ULL * ray_index]) params.visited_expected[ray_index] |= 1;
        if (face == params.expected_faces[2ULL * ray_index + 1]) params.visited_expected[ray_index] |= 2;
    }
    if (face == ray.ignored_face) return;
    const double* vertex = params.vertices + 3 * primitive;
    const double* edge1 = params.edges1 + 3 * primitive;
    const double* edge2 = params.edges2 + 3 * primitive;
    const double cross_x = ray.direction[1] * edge2[2] - ray.direction[2] * edge2[1];
    const double cross_y = ray.direction[2] * edge2[0] - ray.direction[0] * edge2[2];
    const double cross_z = ray.direction[0] * edge2[1] - ray.direction[1] * edge2[0];
    const double determinant = edge1[0] * cross_x + edge1[1] * cross_y + edge1[2] * cross_z;
    if (-1e-8 < determinant && determinant < 1e-8) return;
    const double inverse = 1.0 / determinant;
    const double offset_x = ray.origin[0] - vertex[0];
    const double offset_y = ray.origin[1] - vertex[1];
    const double offset_z = ray.origin[2] - vertex[2];
    const double barycentric_u = (offset_x * cross_x + offset_y * cross_y + offset_z * cross_z) * inverse;
    if (barycentric_u < 0.0 || barycentric_u > 1.0) return;
    const double offset_cross_x = offset_y * edge1[2] - offset_z * edge1[1];
    const double offset_cross_y = offset_z * edge1[0] - offset_x * edge1[2];
    const double offset_cross_z = offset_x * edge1[1] - offset_y * edge1[0];
    const double barycentric_v = (ray.direction[0] * offset_cross_x + ray.direction[1] * offset_cross_y + ray.direction[2] * offset_cross_z) * inverse;
    if (barycentric_v < 0.0 || barycentric_u + barycentric_v > 1.0) return;
    const double distance = (edge2[0] * offset_cross_x + edge2[1] * offset_cross_y + edge2[2] * offset_cross_z) * inverse;
    if (distance <= ray.minimum || distance > ray.maximum) return;
    ++hit.intersections;
    if (fmin(fmin(barycentric_u, barycentric_v), 1.0 - barycentric_u - barycentric_v) < 1e-10) hit.flags |= 2;
    if (distance < hit.distance) {
        hit.second_distance = hit.distance;
        hit.distance = distance;
        hit.face = face;
    } else if (distance < hit.second_distance) {
        hit.second_distance = distance;
    }
}
