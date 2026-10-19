from __future__ import annotations

from functools import cmp_to_key
import math

from leakage_simulator.gpu_cuda_pair_scene import OriginalBvhTopology, build_original_topology


def legacy_hit_fold(candidates, minimum_t, maximum_t):
    best_distance = maximum_t
    best_face = -1
    for face, distance in candidates:
        if distance <= minimum_t or distance > best_distance:
            continue
        if best_face >= 0 and abs(distance - best_distance) <= 1e-10 and face >= best_face:
            continue
        best_distance = distance
        best_face = face
    return (math.inf, -1) if best_face < 0 else (best_distance, best_face)


def isolated_cluster(candidates, minimum_t, maximum_t, capacity=8):
    if any(not math.isfinite(distance) for _, distance in candidates):
        return (), math.inf, 'nonfinite_distance'
    valid = [(face, distance) for face, distance in candidates if minimum_t < distance <= maximum_t]
    if not valid:
        return (), math.inf, 'no_candidate'
    minimum = min(distance for _, distance in valid)
    if abs(minimum) > 1e4:
        return (), math.inf, 'distance_precision_guard'
    cap = min(maximum_t, minimum + 4e-10)
    band = [(face, distance) for face, distance in valid if distance <= minimum + 8e-10]
    if len(band) > capacity:
        return (), cap, 'candidate_overflow'
    if any(distance > cap for _, distance in band):
        return (), cap, 'interference_band'
    return tuple(band), cap, None


def order_cluster_by_original_bvh(candidates, topology, scene, box_entry):
    leaves = {int(face): int(topology.face_leaf[face]) for face, _ in candidates}
    if any(leaf < 0 or not math.isfinite(box_entry(leaf)) for leaf in leaves.values()):
        raise ValueError('A candidate is unreachable in the original BVH')
    entries = {}

    def compare(first, second):
        first_face, second_face = int(first[0]), int(second[0])
        if first_face == second_face:
            return 0
        first_leaf, second_leaf = leaves[first_face], leaves[second_face]
        if first_leaf == second_leaf:
            return -1 if topology.face_order[first_face] < topology.face_order[second_face] else 1
        differing = int(topology.node_code[first_leaf]) ^ int(topology.node_code[second_leaf])
        if differing == 0:
            raise ValueError('Distinct original leaves must have distinct path codes')
        depth = 64 - differing.bit_length()
        ancestor = int(topology.node_path[first_leaf, depth])
        left = int(scene.node_left[ancestor])
        right = int(scene.node_right[ancestor])
        if ancestor not in entries:
            entries[ancestor] = (box_entry(left), box_entry(right))
        left_entry, right_entry = entries[ancestor]
        if not math.isfinite(left_entry) or not math.isfinite(right_entry):
            raise ValueError('Original candidate branches must both be reachable')
        earlier = left if left_entry <= right_entry else right
        first_child = int(topology.node_path[first_leaf, depth + 1])
        return -1 if first_child == earlier else 1

    return tuple(sorted(candidates, key=cmp_to_key(compare)))


def fold_cluster_by_original_bvh(candidates, topology, scene, box_entry, minimum_t, maximum_t):
    ordered = order_cluster_by_original_bvh(candidates, topology, scene, box_entry)
    best_distance = maximum_t
    best_face = -1
    active_leaf = -1
    leaf_allowed = False
    for face, distance in ordered:
        leaf = int(topology.face_leaf[face])
        if leaf != active_leaf:
            active_leaf = leaf
            leaf_allowed = box_entry(leaf) <= best_distance
        if not leaf_allowed or distance <= minimum_t or distance > best_distance:
            continue
        if best_face >= 0 and abs(distance - best_distance) <= 1e-10 and face >= best_face:
            continue
        best_distance = distance
        best_face = face
    return (math.inf, -1) if best_face < 0 else (best_distance, best_face)
