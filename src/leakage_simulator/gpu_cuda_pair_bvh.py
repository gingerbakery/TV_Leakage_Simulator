from __future__ import annotations
import math
import numpy as np
from .gpu_cuda_pair_scene import PAIR_BVH_MAGIC

def make_pair_intersector(cuda, canonical_intersect_bvh, ray_box_entry):
    from numba.cuda import libdevice
    canonical_intersect_bvh = cuda.jit(device=True, inline=False)(canonical_intersect_bvh.py_func)

    @cuda.jit(device=True, inline=True)
    def masked_ray_box_entry(origin_x, origin_y, origin_z, direction_x, direction_y, direction_z, inverse_x, inverse_y, inverse_z, bounds_min, bounds_max, node_index, minimum_t, maximum_t, bias_x_lower, bias_x_upper, bias_y_lower, bias_y_upper, bias_z_lower, bias_z_upper, parallel_mask, negative_mask):
        entry = minimum_t
        exit_distance = maximum_t
        for axis in range(3):
            if axis == 0:
                origin_value = origin_x
                direction_value = direction_x
                inverse_value = inverse_x
                lower_bias = bias_x_lower
                upper_bias = bias_x_upper
            elif axis == 1:
                origin_value = origin_y
                direction_value = direction_y
                inverse_value = inverse_y
                lower_bias = bias_y_lower
                upper_bias = bias_y_upper
            else:
                origin_value = origin_z
                direction_value = direction_z
                inverse_value = inverse_z
                lower_bias = bias_z_lower
                upper_bias = bias_z_upper
            if parallel_mask & np.int32(1) << np.int32(axis) != 0:
                if origin_value < bounds_min[node_index, axis] or origin_value > bounds_max[node_index, axis]:
                    return math.inf
                continue
            if negative_mask & np.int32(1) << np.int32(axis) == 0:
                near_bound = bounds_min[node_index, axis]
                far_bound = bounds_max[node_index, axis]
            else:
                near_bound = bounds_max[node_index, axis]
                far_bound = bounds_min[node_index, axis]
            axis_entry = libdevice.fma_rn(near_bound, inverse_value, lower_bias)
            axis_exit = libdevice.fma_rn(far_bound, inverse_value, upper_bias)
            if axis_entry > entry:
                entry = axis_entry
            if axis_exit < exit_distance:
                exit_distance = axis_exit
            if exit_distance < entry:
                return math.inf
        return entry

    @cuda.jit(device=True, inline=True)
    def isolated_intersect_bvh(ray_index, stack_width, origin_x, origin_y, origin_z, direction_x, direction_y, direction_z, minimum_t, maximum_t, ignored_face, triangle_v0, triangle_edge1, triangle_edge2, node_bounds_min, node_bounds_max, node_left, node_right, node_start, node_count, ordered_faces, traceable_face_mask, stack_entries, stack_nodes):
        first_face = -1
        second_face = -1
        first_distance = math.inf
        second_distance = math.inf
        candidate_count = 0
        uncertain = 0
        if node_count.shape[0] == 0 or maximum_t <= minimum_t:
            return (math.inf, -1, 2)
        inverse_x = 0.0 if abs(direction_x) < 1e-15 else 1.0 / direction_x
        inverse_y = 0.0 if abs(direction_y) < 1e-15 else 1.0 / direction_y
        inverse_z = 0.0 if abs(direction_z) < 1e-15 else 1.0 / direction_z
        root_index = node_start[0]
        bound_x = abs(node_bounds_min[root_index, 0]) + abs(node_bounds_max[root_index, 0]) + 2.0
        margin_x = (abs(origin_x) + bound_x) * abs(inverse_x) * 2.842170943040401e-14
        center_bias_x = -origin_x * inverse_x
        bias_x_lower = center_bias_x - margin_x
        bias_x_upper = center_bias_x + margin_x
        bound_y = abs(node_bounds_min[root_index, 1]) + abs(node_bounds_max[root_index, 1]) + 2.0
        margin_y = (abs(origin_y) + bound_y) * abs(inverse_y) * 2.842170943040401e-14
        center_bias_y = -origin_y * inverse_y
        bias_y_lower = center_bias_y - margin_y
        bias_y_upper = center_bias_y + margin_y
        bound_z = abs(node_bounds_min[root_index, 2]) + abs(node_bounds_max[root_index, 2]) + 2.0
        margin_z = (abs(origin_z) + bound_z) * abs(inverse_z) * 2.842170943040401e-14
        center_bias_z = -origin_z * inverse_z
        bias_z_lower = center_bias_z - margin_z
        bias_z_upper = center_bias_z + margin_z
        parallel_mask = np.int32(0)
        negative_mask = np.int32(0)
        if abs(direction_x) < 1e-12:
            parallel_mask |= np.int32(1)
        if inverse_x < 0.0:
            negative_mask |= np.int32(1)
        if abs(direction_y) < 1e-12:
            parallel_mask |= np.int32(2)
        if inverse_y < 0.0:
            negative_mask |= np.int32(2)
        if abs(direction_z) < 1e-12:
            parallel_mask |= np.int32(4)
        if inverse_z < 0.0:
            negative_mask |= np.int32(4)
        root_entry = masked_ray_box_entry(origin_x, origin_y, origin_z, direction_x, direction_y, direction_z, inverse_x, inverse_y, inverse_z, node_bounds_min, node_bounds_max, node_start[0], minimum_t, maximum_t, bias_x_lower, bias_x_upper, bias_y_lower, bias_y_upper, bias_z_lower, bias_z_upper, parallel_mask, negative_mask)
        if math.isinf(root_entry):
            return (math.inf, -1, 2)
        base = ray_index
        stack_stride = stack_entries.size // stack_width
        best_distance = maximum_t
        best_face = -1
        stack_entries[base] = root_entry
        stack_nodes[base] = node_start[0]
        stack_size = 1
        while stack_size > 0:
            stack_size -= 1
            entry = stack_entries[base + stack_size * stack_stride]
            current_node = stack_nodes[base + stack_size * stack_stride]
            if entry > best_distance + 8e-10:
                continue
            leaf_count = node_count[current_node]
            if leaf_count > 0:
                start = node_start[current_node]
                end = start + leaf_count
                for ordered_index in range(start, end):
                    face_index = ordered_faces[ordered_index]
                    if face_index == ignored_face or not traceable_face_mask[face_index]:
                        continue
                    edge1_x = triangle_edge1[face_index, 0]
                    edge1_y = triangle_edge1[face_index, 1]
                    edge1_z = triangle_edge1[face_index, 2]
                    edge2_x = triangle_edge2[face_index, 0]
                    edge2_y = triangle_edge2[face_index, 1]
                    edge2_z = triangle_edge2[face_index, 2]
                    cross_x = direction_y * edge2_z - direction_z * edge2_y
                    cross_y = direction_z * edge2_x - direction_x * edge2_z
                    cross_z = direction_x * edge2_y - direction_y * edge2_x
                    determinant = edge1_x * cross_x + edge1_y * cross_y + edge1_z * cross_z
                    if -1e-08 < determinant < 1e-08:
                        continue
                    inverse_determinant = 1.0 / determinant
                    offset_x = origin_x - triangle_v0[face_index, 0]
                    offset_y = origin_y - triangle_v0[face_index, 1]
                    offset_z = origin_z - triangle_v0[face_index, 2]
                    u_value = (offset_x * cross_x + offset_y * cross_y + offset_z * cross_z) * inverse_determinant
                    if u_value < 0.0 or u_value > 1.0:
                        continue
                    offset_cross_x = offset_y * edge1_z - offset_z * edge1_y
                    offset_cross_y = offset_z * edge1_x - offset_x * edge1_z
                    offset_cross_z = offset_x * edge1_y - offset_y * edge1_x
                    v_value = (direction_x * offset_cross_x + direction_y * offset_cross_y + direction_z * offset_cross_z) * inverse_determinant
                    if v_value < 0.0 or u_value + v_value > 1.0:
                        continue
                    distance = (edge2_x * offset_cross_x + edge2_y * offset_cross_y + edge2_z * offset_cross_z) * inverse_determinant
                    if distance <= minimum_t or distance > maximum_t:
                        continue
                    if not math.isfinite(distance):
                        uncertain = 1
                        continue
                    if distance < best_distance:
                        best_distance = distance
                        best_face = face_index
                        if candidate_count >= 1 and first_distance > best_distance + 8e-10:
                            if candidate_count == 2 and second_distance <= best_distance + 8e-10:
                                first_face = second_face
                                first_distance = second_distance
                                candidate_count = 1
                            else:
                                candidate_count = 0
                        elif candidate_count == 2 and second_distance > best_distance + 8e-10:
                            candidate_count = 1
                    elif best_face < 0 and distance == best_distance:
                        best_face = face_index
                    if distance > best_distance + 8e-10:
                        continue
                    if u_value < 1e-08 or v_value < 1e-08 or 1.0 - u_value - v_value < 1e-08:
                        uncertain = 1
                    if abs(distance - minimum_t) < 1e-07 or abs(distance - maximum_t) < 1e-07:
                        uncertain = 1
                    if candidate_count == 0:
                        first_face = face_index
                        first_distance = distance
                        candidate_count = 1
                    elif candidate_count == 1:
                        second_face = face_index
                        second_distance = distance
                        candidate_count = 2
                    else:
                        uncertain = 1
                continue
            left_index = node_left[current_node]
            right_index = node_right[current_node]
            left_entry = masked_ray_box_entry(origin_x, origin_y, origin_z, direction_x, direction_y, direction_z, inverse_x, inverse_y, inverse_z, node_bounds_min, node_bounds_max, left_index, minimum_t, maximum_t if maximum_t < best_distance + 8e-10 else best_distance + 8e-10, bias_x_lower, bias_x_upper, bias_y_lower, bias_y_upper, bias_z_lower, bias_z_upper, parallel_mask, negative_mask)
            right_entry = masked_ray_box_entry(origin_x, origin_y, origin_z, direction_x, direction_y, direction_z, inverse_x, inverse_y, inverse_z, node_bounds_min, node_bounds_max, right_index, minimum_t, maximum_t if maximum_t < best_distance + 8e-10 else best_distance + 8e-10, bias_x_lower, bias_x_upper, bias_y_lower, bias_y_upper, bias_z_lower, bias_z_upper, parallel_mask, negative_mask)
            left_hit = not math.isinf(left_entry)
            right_hit = not math.isinf(right_entry)
            needed = 2 if left_hit and right_hit else 1 if left_hit or right_hit else 0
            if stack_size + needed > stack_width:
                return (math.inf, -1, 2)
            if left_hit and right_hit:
                if left_entry <= right_entry:
                    stack_entries[base + stack_size * stack_stride] = right_entry
                    stack_nodes[base + stack_size * stack_stride] = right_index
                    stack_size += 1
                    stack_entries[base + stack_size * stack_stride] = left_entry
                    stack_nodes[base + stack_size * stack_stride] = left_index
                    stack_size += 1
                else:
                    stack_entries[base + stack_size * stack_stride] = left_entry
                    stack_nodes[base + stack_size * stack_stride] = left_index
                    stack_size += 1
                    stack_entries[base + stack_size * stack_stride] = right_entry
                    stack_nodes[base + stack_size * stack_stride] = right_index
                    stack_size += 1
            elif left_hit:
                stack_entries[base + stack_size * stack_stride] = left_entry
                stack_nodes[base + stack_size * stack_stride] = left_index
                stack_size += 1
            elif right_hit:
                stack_entries[base + stack_size * stack_stride] = right_entry
                stack_nodes[base + stack_size * stack_stride] = right_index
                stack_size += 1
        metadata_start = ordered_faces[ordered_faces.shape[0] - 3]
        path_width = ordered_faces[ordered_faces.shape[0] - 2]
        if best_face < 0 or uncertain or candidate_count == 0 or (best_distance > 10000.0):
            return (math.inf, -1, 2)
        near_cap = maximum_t if maximum_t < best_distance + 4e-10 else best_distance + 4e-10
        if first_distance > near_cap or (candidate_count == 2 and second_distance > near_cap):
            return (math.inf, -1, 2)
        first_leaf = ordered_faces[metadata_start + first_face]
        if first_leaf < 0 or first_leaf >= node_start[0]:
            return (math.inf, -1, 2)
        first_entry = ray_box_entry(origin_x, origin_y, origin_z, direction_x, direction_y, direction_z, inverse_x, inverse_y, inverse_z, node_bounds_min, node_bounds_max, first_leaf, minimum_t, near_cap)
        if not math.isfinite(first_entry):
            return (math.inf, -1, 2)
        if candidate_count == 1:
            return (first_distance, first_face, 0)
        second_leaf = ordered_faces[metadata_start + second_face]
        if second_leaf < 0 or second_leaf >= node_start[0]:
            return (math.inf, -1, 2)
        if first_leaf == second_leaf:
            second_entry = first_entry
            first_precedes = ordered_faces[metadata_start + triangle_v0.shape[0] + first_face] < ordered_faces[metadata_start + triangle_v0.shape[0] + second_face]
        else:
            second_entry = ray_box_entry(origin_x, origin_y, origin_z, direction_x, direction_y, direction_z, inverse_x, inverse_y, inverse_z, node_bounds_min, node_bounds_max, second_leaf, minimum_t, near_cap)
            if not math.isfinite(second_entry):
                return (math.inf, -1, 2)
            differing = np.uint64(ordered_faces[metadata_start + 2 * triangle_v0.shape[0] + first_leaf]) ^ np.uint64(ordered_faces[metadata_start + 2 * triangle_v0.shape[0] + second_leaf])
            if differing == 0:
                return (math.inf, -1, 2)
            ancestor_depth = libdevice.clzll(np.int64(differing))
            ancestor = ordered_faces[metadata_start + 2 * triangle_v0.shape[0] + node_start[0] + first_leaf * path_width + ancestor_depth]
            left_child = node_left[ancestor]
            right_child = node_right[ancestor]
            left_entry = ray_box_entry(origin_x, origin_y, origin_z, direction_x, direction_y, direction_z, inverse_x, inverse_y, inverse_z, node_bounds_min, node_bounds_max, left_child, minimum_t, near_cap)
            right_entry = ray_box_entry(origin_x, origin_y, origin_z, direction_x, direction_y, direction_z, inverse_x, inverse_y, inverse_z, node_bounds_min, node_bounds_max, right_child, minimum_t, near_cap)
            if not math.isfinite(left_entry) or not math.isfinite(right_entry):
                return (math.inf, -1, 2)
            earlier = left_child if left_entry <= right_entry else right_child
            first_precedes = ordered_faces[metadata_start + 2 * triangle_v0.shape[0] + node_start[0] + first_leaf * path_width + (ancestor_depth + 1)] == earlier
        if not first_precedes:
            temporary_face = first_face
            first_face = second_face
            second_face = temporary_face
            temporary_distance = first_distance
            first_distance = second_distance
            second_distance = temporary_distance
            temporary_entry = first_entry
            first_entry = second_entry
            second_entry = temporary_entry
        if first_leaf != second_leaf and second_entry > first_distance:
            return (first_distance, first_face, 0)
        if second_distance > first_distance:
            return (first_distance, first_face, 0)
        if abs(second_distance - first_distance) <= 1e-10 and second_face >= first_face:
            return (first_distance, first_face, 0)
        return (second_distance, second_face, 0)

    @cuda.jit(device=True, inline=True)
    def pair_intersect_bvh(ray_index, stack_width, origin_x, origin_y, origin_z, direction_x, direction_y, direction_z, minimum_t, maximum_t, ignored_face, triangle_v0, triangle_edge1, triangle_edge2, node_bounds_min, node_bounds_max, node_left, node_right, node_start, node_count, ordered_faces, traceable_face_mask, stack_entries, stack_nodes):
        if node_count.shape[0] == 0 or node_start[0] <= 0:
            return canonical_intersect_bvh(ray_index, stack_width, origin_x, origin_y, origin_z, direction_x, direction_y, direction_z, minimum_t, maximum_t, ignored_face, triangle_v0, triangle_edge1, triangle_edge2, node_bounds_min, node_bounds_max, node_left, node_right, node_start, node_count, ordered_faces, traceable_face_mask, stack_entries, stack_nodes)
        if ordered_faces.shape[0] < 3 or ordered_faces[ordered_faces.shape[0] - 1] != PAIR_BVH_MAGIC:
            return canonical_intersect_bvh(ray_index, stack_width, origin_x, origin_y, origin_z, direction_x, direction_y, direction_z, minimum_t, maximum_t, ignored_face, triangle_v0, triangle_edge1, triangle_edge2, node_bounds_min, node_bounds_max, node_left, node_right, node_start, node_count, ordered_faces, traceable_face_mask, stack_entries, stack_nodes)
        if not math.isfinite(origin_x) or not math.isfinite(origin_y) or (not math.isfinite(origin_z)) or (abs(origin_x) > 1000000.0) or (abs(origin_y) > 1000000.0) or (abs(origin_z) > 1000000.0) or (not math.isfinite(direction_x)) or (not math.isfinite(direction_y)) or (not math.isfinite(direction_z)) or (0.0 < abs(direction_x) < 1e-12) or (0.0 < abs(direction_y) < 1e-12) or (0.0 < abs(direction_z) < 1e-12):
            return canonical_intersect_bvh(ray_index, stack_width, origin_x, origin_y, origin_z, direction_x, direction_y, direction_z, minimum_t, maximum_t, ignored_face, triangle_v0, triangle_edge1, triangle_edge2, node_bounds_min, node_bounds_max, node_left, node_right, node_start, node_count, ordered_faces, traceable_face_mask, stack_entries, stack_nodes)
        distance, face, status = isolated_intersect_bvh(ray_index, stack_width, origin_x, origin_y, origin_z, direction_x, direction_y, direction_z, minimum_t, maximum_t, ignored_face, triangle_v0, triangle_edge1, triangle_edge2, node_bounds_min, node_bounds_max, node_left, node_right, node_start, node_count, ordered_faces, traceable_face_mask, stack_entries, stack_nodes)
        if status != 0:
            return canonical_intersect_bvh(ray_index, stack_width, origin_x, origin_y, origin_z, direction_x, direction_y, direction_z, minimum_t, maximum_t, ignored_face, triangle_v0, triangle_edge1, triangle_edge2, node_bounds_min, node_bounds_max, node_left, node_right, node_start, node_count, ordered_faces, traceable_face_mask, stack_entries, stack_nodes)
        return (distance, face, 0)
    return pair_intersect_bvh
