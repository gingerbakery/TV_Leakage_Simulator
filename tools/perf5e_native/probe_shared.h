#pragma once
#include <optix.h>
#include <cstdint>

struct ProbeRay {
    double origin[3];
    double direction[3];
    double minimum;
    double maximum;
    int64_t ignored_face;
};

struct ProbeHit {
    double distance;
    double second_distance;
    int64_t face;
    int64_t flags;
    int64_t candidates;
    int64_t intersections;
};

struct ProbeParams {
    OptixTraversableHandle traversable;
    const double* vertices;
    const double* edges1;
    const double* edges2;
    const int64_t* face_ids;
    const ProbeRay* rays;
    ProbeHit* hits;
    double center[3];
    double radius;
    double padding;
    const int64_t* expected_faces;
    uint8_t* visited_expected;
};

static_assert(sizeof(ProbeRay) == 72);
static_assert(sizeof(ProbeHit) == 48);
