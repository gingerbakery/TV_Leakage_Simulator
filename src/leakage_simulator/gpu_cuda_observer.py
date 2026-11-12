"""Optional FP64 resident radiance estimator for analytic optical materials.

One CUDA thread owns a camera sample through all continuations and light-shadow
tests. No per-bounce host transfers. The existing host reducer owns thresholding,
Allowed regions, display quotas and pause checkpoints. Unsupported optical laws
are rejected before execution, never approximated with a different material.
Importing this module does not import Numba or discover a GPU.
"""
from __future__ import annotations

import math
import threading
import time
from types import SimpleNamespace

import numpy as np

from . import gpu_cuda_intersection as backend

CONTRACT = "strict_float64_observer_resident_v1"
SAMPLE_CONTRACT = "observer_counter_latin_v1"
_LOCK = threading.RLock()
_KERNEL = None
_OPTICS_VERIFIED = set()


class ObserverGpuUnsupported(RuntimeError):
    pass


class CellBatch(list):
    """Ordered partial cells, with the number of original camera samples."""
    def __init__(self, cells, sample_count):
        super().__init__(cells)
        self.sample_count = sample_count


def cell_layout(values, start, samples):
    pixels = (start+np.arange(len(values)))//samples
    starts = np.r_[0, np.flatnonzero(np.diff(pixels))+1]
    counts = np.diff(np.r_[starts, len(values)])
    groups = np.repeat(np.arange(len(starts)), counts)
    direct = (values[:, 3] > 0) & (values[:, 2] == 0) & (values[:, 1] > 0)
    has_direct = np.logical_or.reduceat(direct, starts)
    eligible = (values[:, 1] > 0) & (~has_direct[groups] | direct)
    best = np.maximum.reduceat(np.where(eligible, values[:, 1], 0.), starts)
    candidates = np.where(eligible & (values[:, 1] == best[groups]), np.arange(len(values)), len(values))
    selected = np.minimum.reduceat(candidates, starts)
    return pixels[starts], starts, counts, selected


def compact_cell_batch(trace, values, start, view, columns, paths_by_ray):
    samples = int(trace.observer_probe["samples_per_cell"])
    pixels, starts, counts, selected = cell_layout(values, start, samples)
    positive = values[:, 3] > 0
    direct = np.add.reduceat((positive & (values[:, 2] == 0)).astype(np.int64), starts)
    reflected = np.add.reduceat((positive & (values[:, 2] > 0)).astype(np.int64), starts)
    sums = np.add.reduceat(values[:, 0], starts)
    direct_sums = np.add.reduceat(values[:, 7] if values.shape[1] > 7 else np.where(values[:, 2] == 0, values[:, 0], 0.), starts)
    minimum = np.minimum.reduceat(np.where(positive, values[:, 2], trace.config.max_depth+1), starts)
    maximum = np.maximum.reduceat(np.where(positive, values[:, 2], 0), starts)
    cells = []
    for j, pixel in enumerate(pixels):
        chosen = int(selected[j])
        path = paths_by_ray.get(chosen)
        cells.append(dict(view_index=view, row=int(pixel)//columns, column=int(pixel)%columns,
            sample_count=int(counts[j]), sum=float(sums[j]), direct_sum=float(direct_sums[j]), direct_count=int(direct[j]), reflected_count=int(reflected[j]),
            min_depth=int(minimum[j]), max_depth=int(maximum[j]), representative_path=path,
            position=path[-2] if path else None, representative_contribution_nit=float(values[chosen, 1]) if path else 0.))
    return CellBatch(cells, len(values))


def pack_scene(mesh, sources, resolved):
    """Pack immutable geometry and exact supported emission/BRDF laws."""
    from .aim_sampling import aim_sphere_rotation
    from .observer_radiance import source_flux, _quadrature
    from .geometry import vec_norm
    materials = np.zeros((len(mesh.faces), 8), dtype=np.float64)
    normals = np.asarray([mesh.normal(i) for i in range(len(mesh.faces))], dtype=np.float64).reshape(-1, 3)
    for i in range(len(mesh.faces)):
        p = resolved[i].profile
        if p.bsdf_asset_id:
            raise ObserverGpuUnsupported("observer_resident_bsdf_requires_host")
        diffuse, delta, gaussian = (1., 0., 0.) if p.scatter_model == "lambertian" else (0., 1., 0.) if p.scatter_model == "specular" else (0., 0., 1.) if p.scatter_model == "gaussian" else (1-p.specular_ratio, p.specular_ratio if p.gaussian_sigma_deg <= .01 else 0., p.specular_ratio if p.gaussian_sigma_deg > .01 else 0.) if p.scatter_model == "mixed" else (0., 0., 0.)
        materials[i] = p.reflectance, p.roughness, diffuse, delta, float(p.scatter_model != "none"), gaussian, math.radians(max(1e-6, p.gaussian_sigma_deg)), float(bool(mesh.metadata(i).get("preview_blocker_id")))
    # Triangles carry emitter identity, CAD face (-1=non-opaque virtual), and
    # cumulative area within that emitter. Adjacent triangle seams emit once.
    triangles, source_rows = [], []
    for source_index, source in enumerate(sources):
        emitter, geometry, weights, area = source
        row = np.zeros(15, dtype=np.float64)
        row[0:3] = len(triangles), len(geometry), area
        row[3] = source_flux(emitter, area)/(area*1e-6)
        row[4] = 0 if emitter.direction_distribution == "lambertian" else 1
        row[5] = float(emitter.emission_direction == "both")
        row[6] = float(emitter.normal_flip)
        if emitter.normal_mode == "custom" and emitter.custom_normal is not None:
            row[7] = 1
            row[8:11] = vec_norm(emitter.custom_normal)
        aim = emitter.aim
        if aim is not None and aim.enabled:
            if aim.mode != "sphere" or aim.distribution not in ("uniform_solid_angle", "lambertian"):
                raise ObserverGpuUnsupported("observer_resident_aim_requires_host")
            row[4] = 2 if aim.distribution == "uniform_solid_angle" else 3
            row[8:11] = tuple(r[2] for r in aim_sphere_rotation(aim))
            low, high = map(math.radians, (aim.sphere_upper_deg, aim.sphere_lower_deg))
            row[11:13] = low, high
            h = min(high, math.pi/2) if row[4] == 3 else high
            row[13] = math.pi*math.sin(h+low)*math.sin(h-low) if row[4] == 3 else 4*math.pi*math.sin((high+low)/2)*math.sin((high-low)/2)
            if row[13] <= 0:
                raise ObserverGpuUnsupported("observer_resident_aim_zero_mass")
        for (triangle, normal, face), weight in zip(geometry, weights):
            face_id = -1 if face is None or mesh.metadata(face).get("trace_excluded", False) else face
            triangles.append([*triangle[0], *triangle[1], *triangle[2], *normal, source_index, face_id, weight])
        source_rows.append(row)
    # CAD first hits already identify the emitting face. Index those records
    # instead of scanning every source triangle for every camera/bounce sample.
    face_records = [[] for _ in mesh.faces]
    virtual_records = [[] for _ in sources]
    for j, triangle in enumerate(triangles):
        face_id, source_id = int(triangle[13]), int(triangle[12])
        (face_records[face_id] if face_id >= 0 else virtual_records[source_id]).append(j)
    def compressed(rows):
        offsets = np.zeros(len(rows)+1, dtype=np.int64)
        offsets[1:] = np.cumsum([len(row) for row in rows])
        return offsets, np.asarray([i for row in rows for i in row], dtype=np.int64)
    face_offsets, face_records = compressed(face_records)
    virtual_offsets, virtual_records = compressed(virtual_records)
    from .observer_source_index import ordered_triangle_bounds
    node_bounds, node_ranges, light_trees = [], [], []
    # Build independent ordered trees within each emitter, preserving seams.
    for light in range(len(sources)):
        start, end = int(virtual_offsets[light]), int(virtual_offsets[light+1])
        geometry = [np.asarray(triangles[int(virtual_records[i])][:9]).reshape(3,3) for i in range(start,end)]
        bounds, ranges = ordered_triangle_bounds(geometry)
        root = len(node_bounds)
        if len(ranges):
            ranges[:,2] += root
            leaf = ranges[:,0] >= 0
            ranges[leaf,:2] += start
        node_bounds.extend(bounds)
        node_ranges.extend(ranges)
        light_trees.append((root,len(node_bounds)))
    return dict(materials=materials, normals=normals,
        lights=np.asarray(source_rows, dtype=np.float64).reshape(-1, 15),
        triangles=np.asarray(triangles, dtype=np.float64).reshape(-1, 15),
        face_light_offsets=face_offsets, face_light_records=face_records,
        virtual_light_offsets=virtual_offsets, virtual_light_records=virtual_records,
        virtual_node_bounds=np.asarray(node_bounds,dtype=np.float64).reshape(-1,6),
        virtual_node_ranges=np.asarray(node_ranges,dtype=np.int64).reshape(-1,3),
        virtual_light_trees=np.asarray(light_trees,dtype=np.int64).reshape(-1,2),
        quadrature=np.column_stack(_quadrature()))


def make_kernel(cuda, intersect, helpers=None):
    """Injectable decorators/intersector also permit a CPU equation oracle."""
    device = cuda.jit(device=True, inline=True, fastmath=False)

    @device
    def dot(a, b):
        return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]

    @device
    def add(a, b, scale):
        return a[0]+b[0]*scale, a[1]+b[1]*scale, a[2]+b[2]*scale

    @device
    def neg(a):
        return -a[0], -a[1], -a[2]

    @device
    def cross(a, b):
        return a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]

    @device
    def norm(a):
        length = math.sqrt(dot(a, a))
        return a[0]/length, a[1]/length, a[2]/length

    @device
    def random(key, lane):
        # 32-bit counter avalanche, exactly representable on CPU and CUDA.
        # Two independent words provide a 53-bit uniform; no mutable RNG state.
        x = (key ^ (lane*747796405+2891336453)) & 0xffffffff
        x = ((x ^ (x >> 16))*2246822507) & 0xffffffff
        x = ((x ^ (x >> 13))*3266489909) & 0xffffffff
        x = (x ^ (x >> 16)) & 0xffffffff
        y = (x ^ 277803737) & 0xffffffff
        y = ((y ^ (y >> 16))*2246822507) & 0xffffffff
        y = ((y ^ (y >> 13))*3266489909) & 0xffffffff
        y = (y ^ (y >> 16)) & 0xffffffff
        return ((x >> 5)*67108864.+(y >> 6))*(1./9007199254740992.)

    @device
    def rho(materials, face, cosine, angle):
        base = materials[face, 0]
        if base <= 0 or not angle:
            return base
        grazing = max(0., (0.7-max(0., min(1., cosine)))/0.7)**5
        return min(1., base+(1-base)*grazing*(.25+.75*(1-materials[face, 1])))

    @device
    def balance(a, b):
        if a <= 0:
            return 0.
        scale = max(a, b)
        return (a/scale)/(a/scale+b/scale)

    @device
    def angle_between(a, b):
        cr = cross(a, b)
        return math.atan2(math.sqrt(dot(cr, cr)), dot(a, b))

    @device
    def acceptance(quadrature, sigma, cosine):
        cosine = max(0., min(1., cosine))
        gamma = math.acos(cosine)
        limit = min(math.pi/2, sigma*math.sqrt(-2*math.log(1e-12)))
        split = max(0., min(limit, math.asin(cosine)-1e-9))
        middle = -math.expm1(-split*split/(2*sigma*sigma))
        upper = -math.expm1(-limit*limit/(2*sigma*sigma))
        total = 0.
        for segment in range(2):
            low, high = (0., middle) if segment == 0 else (middle, upper)
            if high <= low:
                continue
            subtotal = 0.
            for i in range(quadrature.shape[0]):
                cdf = low+(quadrature[i, 0]+1)*(high-low)/2
                theta = sigma*math.sqrt(-2*math.log1p(-cdf))
                denominator = math.sin(gamma)*math.sin(theta)
                ratio = (1e-9-cosine*math.cos(theta))/denominator if denominator > 1e-30 else -1.
                fraction = math.acos(max(-1., min(1., ratio)))/math.pi
                if gamma == 0:
                    fraction = 1. if math.cos(theta) > 1e-9 else 0.
                subtotal += quadrature[i, 1]*fraction
            total += subtotal*(high-low)/2
        return max(0., min(1., total))

    @device
    def delta_probability(materials, face, normal, wo, quadrature):
        if materials[face, 0] <= 0:
            return 0.
        delta = materials[face, 3]
        if materials[face, 5] > 0:
            a = acceptance(quadrature, materials[face, 6], dot(normal, wo))
            delta += materials[face, 5]*(1-a)**32
        return min(1., delta)

    @device
    def evaluate_brdf(materials, face, normal, wi, wo, angle, quadrature):
        ci, co = dot(normal, wi), dot(normal, wo)
        if ci <= 0 or co <= 0:
            return 0.
        result = materials[face, 2]/math.pi
        gaussian = materials[face, 5]
        if gaussian > 0:
            mirror = add(neg(wi), normal, 2*ci)
            theta = angle_between(mirror, wo)
            sigma = materials[face, 6]
            if theta < min(math.pi/2, sigma*math.sqrt(-2*math.log(1e-12))) and co > 1e-9:
                a = acceptance(quadrature, sigma, ci)
                if a > 0:
                    raw = math.exp(-theta*theta/(2*sigma*sigma))/(2*math.pi*sigma*sigma)
                    if theta > 1e-8:
                        raw *= theta/math.sin(theta)
                    mass = -math.expm1(32*math.log1p(-a)) if a < 1 else 1.
                    result += gaussian*raw*mass/a/co
        return rho(materials, face, ci, angle)*result

    @device
    def continuous_pdf(materials, face, normal, wo, wi, quadrature):
        cosine = dot(normal, wi)
        if cosine <= 0:
            return 0.
        probability = delta_probability(materials, face, normal, wo, quadrature)
        q = cosine/math.pi
        if materials[face, 5] > 0:
            sigma = materials[face, 6]
            mirror = add(neg(wo), normal, 2*dot(normal, wo))
            theta = angle_between(mirror, wi)
            a = acceptance(quadrature, sigma, dot(normal, wo))
            qg = 0.
            if theta < min(math.pi/2, sigma*math.sqrt(-2*math.log(1e-12))) and cosine > 1e-9 and a > 0:
                qg = math.exp(-theta*theta/(2*sigma*sigma))/(2*math.pi*sigma*sigma*a)
                if theta > 1e-8:
                    qg *= theta/math.sin(theta)
            q = .5*q+.5*qg
        return (1-probability)*q

    if helpers is not None:
        helpers.update(acceptance=acceptance, brdf=evaluate_brdf, pdf=continuous_pdf, delta=delta_probability)

    @device
    def emission(lights, light, normal, outgoing):
        mode = int(lights[light, 4])
        projected = abs(dot(normal, outgoing))
        if projected <= 1e-14:
            return 0.
        n = normal
        if lights[light, 7] == 1:
            n = lights[light, 8], lights[light, 9], lights[light, 10]
        if lights[light, 6] == 1:
            n = neg(n)
        cosine = dot(n, outgoing)
        if mode >= 2:
            axis = lights[light, 8], lights[light, 9], lights[light, 10]
            c = max(-1., min(1., dot(axis, outgoing)))
            cr = cross(axis, outgoing)
            theta = math.atan2(math.sqrt(dot(cr, cr)), c)
            if theta < lights[light, 11]-1e-12 or theta > lights[light, 12]+1e-12:
                return 0.
            density = 1./lights[light, 13] if mode == 2 else max(0., c)/lights[light, 13]
        elif mode == 1:
            density = 1./(4*math.pi)
        else:
            density = abs(cosine)/(2*math.pi) if lights[light, 5] == 1 else max(0., cosine)/math.pi
        return lights[light, 3]*density/projected

    @device
    def triangle_hit(origin, direction, triangles, j, epsilon):
        a = triangles[j, 0], triangles[j, 1], triangles[j, 2]
        b = triangles[j, 3], triangles[j, 4], triangles[j, 5]
        c = triangles[j, 6], triangles[j, 7], triangles[j, 8]
        e1, e2 = add(b, a, -1.), add(c, a, -1.)
        p = cross(direction, e2)
        det = dot(e1, p)
        if abs(det) < 1e-14:
            return math.inf
        offset = add(origin, a, -1.)
        u = dot(offset, p)/det
        q = cross(offset, e1)
        v, t = dot(direction, q)/det, dot(e2, q)/det
        if u >= -1e-10 and v >= -1e-10 and u+v <= 1+1e-10 and t >= epsilon:
            return t
        return math.inf

    @device
    def next_virtual(node, position, end_node, origin, direction, epsilon, maximum, bounds, ranges, records):
        while node < end_node:
            start, end, skip = ranges[node,0], ranges[node,1], ranges[node,2]
            if position < 0:
                enter, leave = epsilon, maximum
                for axis in range(3):
                    if direction[axis] == 0:
                        if origin[axis] < bounds[node,axis] or origin[axis] > bounds[node,axis+3]:
                            leave = -math.inf
                            break
                    else:
                        a = (bounds[node,axis]-origin[axis])/direction[axis]
                        b = (bounds[node,axis+3]-origin[axis])/direction[axis]
                        enter, leave = max(enter,min(a,b)), min(leave,max(a,b))
                if enter > leave:
                    node, position = skip, -1
                    continue
                if start < 0:
                    node += 1
                    continue
                position = start
            if position < end:
                return records[position], node, position+1
            node, position = skip, -1
        return -1, end_node, -1

    @device
    def save_path(paths, vertices, ray, count, point, launch):
        for a in range(3):
            paths[ray, 0, a] = point[a]
            paths[ray, count+1, a] = launch[a]
        for j in range(count):
            for a in range(3):
                paths[ray, j+1, a] = vertices[ray, count-1-j, a]

    @cuda.jit(fastmath=False)
    def kernel(start, count, view, seed, samples, columns, plan, sensor_rays, epsilon, max_depth, angle, k_brdf,
               materials, normals, lights, triangles, quadrature,
               face_light_offsets, face_light_records, virtual_light_offsets, virtual_light_records,
               virtual_node_bounds, virtual_node_ranges, virtual_light_trees, stack_width,
               v0, edge1, edge2, bmin, bmax, left, right, node_start, node_count, order, mask,
               stack_entries, stack_nodes, vertices, paths, output):
        ray = cuda.grid(1)
        if ray >= count:
            return
        index = start+ray
        pixel, repeat = index//samples, index%samples
        row, column = pixel//columns, pixel%columns
        key = (seed*2654435761+view*1000003+pixel*9176) & 0xffffffff
        shift = int(random(key, 0)*samples)
        jx = (repeat+random(key, repeat*2+1))/samples
        jy = ((repeat+shift)%samples+random(key, repeat*2+2))/samples
        launch = (plan[0]+plan[3]*(column+jx)+plan[6]*(row+jy),
                  plan[1]+plan[4]*(column+jx)+plan[7]*(row+jy),
                  plan[2]+plan[5]*(column+jx)+plan[8]*(row+jy))
        origin = launch
        direction = -plan[9], -plan[10], -plan[11]
        camera_weight = 1.
        if sensor_rays.shape[0] > 0:
            launch = sensor_rays[ray, 0], sensor_rays[ray, 1], sensor_rays[ray, 2]
            origin = launch
            direction = sensor_rays[ray, 3], sensor_rays[ray, 4], sensor_rays[ray, 5]
            camera_weight = sensor_rays[ray, 6]
        ray_key = (key+repeat*2246822519) & 0xffffffff
        throughput, value, best, direct_value = 1., 0., 0., 0.
        previous_delta, previous_pdf = True, 0.
        vertex_count = best_count = best_depth = depth_limited = queries = error = 0
        for depth in range(max_depth+1):
            t, face, status = intersect(ray, stack_width, origin[0], origin[1], origin[2],
                direction[0], direction[1], direction[2], epsilon, math.inf, -1,
                v0, edge1, edge2, bmin, bmax, left, right, node_start, node_count, order, mask, stack_entries, stack_nodes)
            queries += 1
            if status != 0:
                error = 1
                break
            # One hit per emitter, including seams of its virtual triangulation.
            for light in range(lights.shape[0]):
                face_start = face_light_offsets[face] if face >= 0 else 0
                face_end = face_light_offsets[face+1] if face >= 0 else 0
                face_pos = face_start
                end_node = virtual_light_trees[light,1]
                virtual_j, node, position = next_virtual(virtual_light_trees[light,0], -1, end_node,
                    origin, direction, epsilon, t+1e-9, virtual_node_bounds, virtual_node_ranges, virtual_light_records)
                previous_t = -math.inf
                while virtual_j >= 0 or face_pos < face_end:
                    if virtual_j >= 0 and (face_pos >= face_end or virtual_j < face_light_records[face_pos]):
                        j = virtual_j
                        virtual_j, node, position = next_virtual(node, position, end_node, origin, direction,
                            epsilon, t+1e-9, virtual_node_bounds, virtual_node_ranges, virtual_light_records)
                    else:
                        j = face_light_records[face_pos]
                        face_pos += 1
                    if int(triangles[j, 12]) != light:
                        continue
                    source_face = int(triangles[j, 13])
                    hit_t = triangle_hit(origin, direction, triangles, j, epsilon) if source_face < 0 else t
                    if (source_face < 0 and not hit_t <= t+1e-9) or (source_face >= 0 and source_face != face):
                        continue
                    if source_face < 0 and face >= 0 and materials[face, 7] > 0 and abs(hit_t-t) <= 1e-9:
                        continue
                    if math.isinf(hit_t) or abs(hit_t-previous_t) <= 1e-10:
                        continue
                    previous_t = hit_t
                    point = add(origin, direction, hit_t)
                    normal = triangles[j, 9], triangles[j, 10], triangles[j, 11]
                    weight = 1.
                    if not previous_delta and vertex_count:
                        prev = vertices[ray, vertex_count-1, 0], vertices[ray, vertex_count-1, 1], vertices[ray, vertex_count-1, 2]
                        delta = add(point, prev, -1.)
                        cosine = abs(dot(normal, neg(direction)))
                        pdf = dot(delta, delta)/(lights[light, 2]*cosine*lights.shape[0]) if cosine > 1e-14 else 0.
                        weight = balance(previous_pdf, pdf)
                    contribution = throughput*emission(lights, light, normal, neg(direction))*weight
                    value += contribution
                    if depth == 0:
                        direct_value += contribution
                    if contribution > 0 and (best_count == 0 or (depth == 0 and best_depth > 0) or ((depth == 0) == (best_depth == 0) and contribution > best)):
                        best, best_count, best_depth = contribution, vertex_count+2, depth
                        save_path(paths, vertices, ray, vertex_count, point, launch)
            if face < 0:
                break
            if depth >= max_depth:
                depth_limited = int(k_brdf > 0 and materials[face, 0] > 0 and materials[face, 4] > 0)
                break
            if k_brdf <= 0 or materials[face, 4] == 0:
                break
            point = add(origin, direction, t)
            normal = normals[face, 0], normals[face, 1], normals[face, 2]
            if dot(direction, normal) > 0:
                normal = neg(normal)
            wo = neg(direction)
            # Area light NEE, using the same balance MIS as the reference.
            lane_base = depth*4096
            if lights.shape[0] and materials[face, 2]+materials[face, 5] > 0:
                light = min(lights.shape[0]-1, int(random(ray_key, lane_base+3)*lights.shape[0]))
                low, high = int(lights[light, 0]), int(lights[light, 0]+lights[light, 1])-1
                draw = random(ray_key, lane_base+4)*lights[light, 2]
                while low < high:
                    middle = (low+high)//2
                    if triangles[middle, 14] < draw:
                        low = middle+1
                    else:
                        high = middle
                selected = low
                root, fraction = math.sqrt(random(ray_key, lane_base+5)), random(ray_key, lane_base+6)
                lamp = ((1-root)*triangles[selected, 0]+root*(1-fraction)*triangles[selected, 3]+root*fraction*triangles[selected, 6],
                        (1-root)*triangles[selected, 1]+root*(1-fraction)*triangles[selected, 4]+root*fraction*triangles[selected, 7],
                        (1-root)*triangles[selected, 2]+root*(1-fraction)*triangles[selected, 5]+root*fraction*triangles[selected, 8])
                delta = add(lamp, point, -1.)
                distance2 = dot(delta, delta)
                if distance2 > (4*epsilon)**2:
                    distance = math.sqrt(distance2)
                    wi = norm(delta)
                    light_normal = triangles[selected, 9], triangles[selected, 10], triangles[selected, 11]
                    ci, cl = max(0., dot(normal, wi)), abs(dot(light_normal, neg(wi)))
                    f = evaluate_brdf(materials, face, normal, wi, wo, angle, quadrature)
                    if f > 0 and cl > 1e-14:
                        shadow = add(point, wi, epsilon)
                        st, sf, status = intersect(ray, stack_width, shadow[0], shadow[1], shadow[2], wi[0], wi[1], wi[2],
                            epsilon, distance-epsilon+1e-9, -1, v0, edge1, edge2, bmin, bmax, left, right, node_start, node_count, order, mask, stack_entries, stack_nodes)
                        queries += 1
                        if status != 0:
                            error = 1
                            break
                        if sf < 0 or (abs(st-(distance-epsilon)) <= 1e-9 and materials[sf, 7] == 0):
                            pdf = distance2/(lights[light, 2]*cl*lights.shape[0])
                            q = continuous_pdf(materials, face, normal, wo, wi, quadrature)
                            contribution = throughput*f*ci*emission(lights, light, light_normal, neg(wi))/pdf*balance(pdf, q)
                            value += contribution
                            if contribution > 0 and (best_count == 0 or (best_depth > 0 and contribution > best)):
                                for a in range(3):
                                    vertices[ray, vertex_count, a] = point[a]
                                best, best_count, best_depth = contribution, vertex_count+3, depth+1
                                save_path(paths, vertices, ray, vertex_count+1, lamp, launch)
            if error:
                break
            mirror = norm(add(direction, normal, -2*dot(direction, normal)))
            reflectance = rho(materials, face, dot(normal, wo), angle)
            probability = delta_probability(materials, face, normal, wo, quadrature) if reflectance > 0 else 0.
            if random(ray_key, lane_base+7) < probability:
                new_direction, weight, previous_delta, previous_pdf = mirror, reflectance, True, 0.
            else:
                if probability >= 1:
                    break
                helper = (0., 1., 0.) if abs(normal[0]) > .9 else (1., 0., 0.)
                u = norm(cross(helper, normal))
                v = cross(normal, u)
                radial = math.sqrt(random(ray_key, lane_base+8))
                phi = 2*math.pi*random(ray_key, lane_base+9)
                cosine = math.sqrt(max(0., 1-radial*radial))
                new_direction = norm((u[0]*radial*math.cos(phi)+v[0]*radial*math.sin(phi)+normal[0]*cosine,
                                      u[1]*radial*math.cos(phi)+v[1]*radial*math.sin(phi)+normal[1]*cosine,
                                      u[2]*radial*math.cos(phi)+v[2]*radial*math.sin(phi)+normal[2]*cosine))
                if materials[face, 5] > 0 and random(ray_key, lane_base+11) < .5:
                    sigma = materials[face, 6]
                    limit = min(math.pi/2, sigma*math.sqrt(-2*math.log(1e-12)))
                    mass = -math.expm1(-limit*limit/(2*sigma*sigma))
                    helper = (0., 1., 0.) if abs(mirror[0]) > .9 else (1., 0., 0.)
                    u = norm(cross(helper, mirror))
                    v = cross(mirror, u)
                    accepted = False
                    for attempt in range(1024):
                        theta = sigma*math.sqrt(-2*math.log1p(-random(ray_key, lane_base+12+attempt*2)*mass))
                        phi = 2*math.pi*random(ray_key, lane_base+13+attempt*2)
                        new_direction = norm((mirror[0]*math.cos(theta)+math.sin(theta)*(u[0]*math.cos(phi)+v[0]*math.sin(phi)),
                            mirror[1]*math.cos(theta)+math.sin(theta)*(u[1]*math.cos(phi)+v[1]*math.sin(phi)),
                            mirror[2]*math.cos(theta)+math.sin(theta)*(u[2]*math.cos(phi)+v[2]*math.sin(phi))))
                        if dot(normal, new_direction) > 1e-9:
                            accepted = True
                            break
                    if not accepted:
                        error = 2
                        break
                ci = max(0., dot(normal, new_direction))
                previous_pdf = continuous_pdf(materials, face, normal, wo, new_direction, quadrature)
                f = evaluate_brdf(materials, face, normal, new_direction, wo, angle, quadrature)
                weight = f*ci/previous_pdf if previous_pdf > 0 else 0.
                previous_delta = False
            throughput *= weight
            if throughput <= 0:
                break
            if depth >= 3:
                survival = min(.95, max(.05, throughput))
                if random(ray_key, lane_base+10) >= survival:
                    break
                throughput /= survival
            for a in range(3):
                vertices[ray, vertex_count, a] = point[a]
            vertex_count += 1
            origin, direction = add(point, new_direction, epsilon), new_direction
        output[ray, 0] = value*camera_weight
        output[ray, 1] = best*camera_weight
        output[ray, 2] = best_depth
        output[ray, 3] = best_count
        output[ray, 4] = depth_limited
        output[ray, 5] = queries
        output[ray, 6] = error
        output[ray, 7] = direct_value*camera_weight
    return kernel


def camera_plan(plan, center):
    outward, u, v = plan["outward"], plan["u"], plan["v"]
    return np.array([*(center[i]+outward[i]*plan["distance"]+u[i]*plan["min_u"]+v[i]*plan["min_v"] for i in range(3)),
        *(u[i]*plan["width"]/plan["columns"] for i in range(3)),
        *(v[i]*plan["height"]/plan["rows"] for i in range(3)), *outward], dtype=np.float64)


def make_path_gather(cuda):
    @cuda.jit(fastmath=False)
    def gather(indices, lengths, paths, compact):
        i = cuda.grid(1)
        if i < indices.size:
            for j in range(lengths[i]):
                for axis in range(3):
                    compact[i, j, axis] = paths[indices[i], j, axis]
    return gather


def reference_chunk(trace, host, plan, center, view, start, count, *, compiled_helpers=False, sensor=None):
    """CPU equation oracle with identical counter samples; not GPU evidence."""
    class Cpu:
        index = 0
        def jit(self, **kwargs):
            if kwargs.get("device") and compiled_helpers:
                from numba import njit
                return njit(fastmath=False)
            return lambda fn: fn
        def grid(self, dimensions):
            return self.index
    cpu = Cpu()
    mesh = trace.mesh
    def intersect(ray, width, ox, oy, oz, dx, dy, dz, minimum, maximum, ignored, *unused):
        hit = mesh.intersect_ray((ox, oy, oz), (dx, dy, dz), min_t=minimum, max_t=maximum, backend="bvh")
        return (hit.t, hit.face_index, 0) if hit else (math.inf, -1, 0)
    kernel = make_kernel(cpu, intersect)
    depth, c = trace.config.max_depth, trace.config
    vertices = np.zeros((count, depth+3, 3), dtype=np.float64)
    paths = np.zeros_like(vertices)
    output = np.zeros((count, 8), dtype=np.float64)
    dummy = np.empty(0)
    sensor_rays = sensor.rays(trace.config.seed, start, count) if sensor is not None else np.empty((0, 7), dtype=np.float64)
    for i in range(count):
        cpu.index = i
        kernel(start, count, view, int(c.seed)&0xffffffff, int(trace.observer_probe["samples_per_cell"]),
            plan["columns"], camera_plan(plan, center), sensor_rays, max(c.epsilon_mm, 1e-8), depth, c.angle_dependent_reflectance, c.k_brdf,
            host["materials"], host["normals"], host["lights"], host["triangles"], host["quadrature"],
            host["face_light_offsets"], host["face_light_records"], host["virtual_light_offsets"], host["virtual_light_records"],
            host["virtual_node_bounds"], host["virtual_node_ranges"], host["virtual_light_trees"], 2,
            dummy, dummy, dummy, dummy, dummy, dummy, dummy, dummy, dummy, dummy, dummy,
            dummy, dummy, vertices, paths, output)
    if np.any(output[:, 6] != 0):
        raise RuntimeError("observer_reference_query_or_sampling_failed")
    return output, paths


def verify_resident_optics(session_type):
    """Real-device analytic/CPU-oracle guard, cached only after ALL cases pass."""
    from .geometry import TriangleMesh
    from .observer_probe import _source_geometry, sampling_plan
    from .optics import OpticalPropertyResolver
    from .types import EmitterSpec, OpticalProfile, RayTraceConfig
    for mode in ("direct", "blocked", "coplanar", "coplanar_blocked", "coplanar_blocker", "specular", "lambertian", "gaussian", "mixed"):
        reflected = mode not in ("direct", "blocked", "coplanar", "coplanar_blocked", "coplanar_blocker")
        mesh = TriangleMesh()
        def plate(z):
            ids = [mesh.add_vertex(p) for p in ((-20, -20, z), (20, -20, z), (20, 20, z), (-20, 20, z))]
            mesh.add_face(ids[0], ids[1], ids[2], "surface")
            mesh.add_face(ids[0], ids[2], ids[3], "surface")
        plate(-5. if mode.startswith("coplanar") else 0. if reflected else 9.)
        if mode == "coplanar_blocker":
            for face in range(len(mesh.faces)):
                mesh.metadata(face)["preview_blocker_id"] = "preflight-cover"
        if mode in ("blocked", "coplanar_blocked"):
            plate(-7.)
        profile = OpticalProfile(profile_id="surface", reflectance=.9 if reflected else 0., scatter_model=mode if reflected else "none", gaussian_sigma_deg=18., specular_ratio=.3)
        trace = SimpleNamespace(mesh=mesh,
            emitters=[EmitterSpec(emitter_id="preflight", emitter_type="datum_plane", center=(0., 0., -5.),
                u_axis=(1., 0., 0.), v_axis=(0., 1. if reflected else -1., 0.), width_mm=40., height_mm=40.,
                power_mode="set_luminance", luminance_nit=100., ray_count=1)],
            config=RayTraceConfig(max_depth=4, seed=42, angle_dependent_reflectance=False, compute_backend="gpu_cuda"),
            observer_probe=dict(measurement="radiance", horizontal_deg=0., vertical_deg=0., range_deg=0., step_deg=1.,
                resolution=8, samples_per_cell=2, coverage="local", search_mode="preview"))
        resolved = OpticalPropertyResolver(mesh, [profile], []).resolve_all()
        probe = session_type(trace, _source_geometry(mesh, trace.emitters), resolved, _verify_optics=False)
        plan = sampling_plan(trace.observer_probe, (-10., -10., -10.), (10., 10., 10.), "neg_z", 0)[0]
        completed, execution, _ = probe.run(plan, (0., 0., 0.), 0, 0, 32)
        if getattr(execution, "cpu_fallback", False) or not probe.validated:
            raise ObserverGpuUnsupported(f"observer_resident_optical_preflight_failed:{mode}")
        analytic = {"direct": 100., "coplanar": 100., "blocked": 0., "coplanar_blocked": 0., "coplanar_blocker": 0., "specular": 90.}.get(mode)
        values = np.array([item[1] for item in completed])
        if (analytic is not None and not np.allclose(values, analytic, rtol=1e-9, atol=1e-10)) or (reflected and not np.any(values > 0)):
            raise ObserverGpuUnsupported(f"observer_resident_optical_preflight_mismatch:{mode}")


class ResidentObserver:
    def __init__(self, trace, sources, resolved, *, _verify_optics=True, sensor=None):
        global _KERNEL
        prepare_started = time.perf_counter()
        self.trace = trace
        self.sensor = sensor
        self.host = pack_scene(trace.mesh, sources, resolved)
        # Real production preflight, never infer readiness from package imports.
        preflight = backend.preflight_gpu_cuda()
        if not (preflight.available and preflight.strict_float64 and preflight.kernel_executed and preflight.kernel_verified and preflight.preflight_scope == "production_ray_bvh" and backend.PROVIDER_CONTRACT == "strict_float64_bvh_v1"):
            raise ObserverGpuUnsupported(preflight.reason_code or "observer_resident_preflight_failed")
        self.capability = backend.probe_gpu_cuda()
        self.cuda = backend._CUDA
        self.scene = trace.mesh.prepare_gpu_cuda_scene()
        self.device_scene, self.reused_scene = backend._ensure_device_scene(self.scene, self.capability)
        self.static = {k: self.cuda.to_device(v) for k, v in self.host.items()}
        self.workspace = None
        with _LOCK:
            if _KERNEL is None:
                from .gpu_cuda_resident_wavefront import _make_kernel
                captured = []
                def capture(cuda, intersector, box):
                    captured.append(intersector)
                    return intersector
                # Reuse the production strict FP64 BVH device routine.
                _make_kernel(_intersector_factory=capture)
                _KERNEL = make_kernel(self.cuda, captured[0])
        self.kernel = _KERNEL
        self.path_gather = make_path_gather(self.cuda)
        self.compact_paths = None
        self.validated = False
        self.failure_reason = None
        self.prepare_sec = time.perf_counter()-prepare_started
        self.validation_sec = 0.
        self.compact_cells = False
        if _verify_optics:
            with _LOCK:
                identity = (self.capability.device_id, id(self.kernel))
                if identity not in _OPTICS_VERIFIED:
                    verify_resident_optics(type(self))
                    _OPTICS_VERIFIED.add(identity)
        self.prepare_sec = time.perf_counter()-prepare_started

    def batch_size(self, requested):
        # Two bounded geometry arrays; no allocation of the entire 180° grid.
        # A legacy excluded Body may contain many virtual emitting triangles.
        # Bound that work per launch as well as path memory for Stop/progress.
        virtual_count = len(self.host["virtual_light_records"])
        work_limit = max(1, 262144//max(1, virtual_count*(self.trace.config.max_depth+1)))
        return max(1, min(requested, 262144//(self.trace.config.max_depth+3), work_limit))

    def run(self, plan, center, view, start, count):
        if self.failure_reason is None:
            try:
                return self._run_gpu(plan, center, view, start, count)
            except Exception as exc:
                if getattr(self, "sensor", None) is not None:
                    raise RuntimeError(f"observer_sensor_gpu_failed: {exc}") from exc
                self.failure_reason = str(exc) or type(exc).__name__
                print(f"[OBSERVER] GPU resident failed | {self.failure_reason} | replaying same samples on CPU", flush=True)
        # Replay the untouched chunk with EXACTLY the same counter samples.
        # Never substitute an optical law or double-commit a partial GPU result.
        began = time.perf_counter()
        values, paths = reference_chunk(self.trace, self.host, plan, center, view, start, count, sensor=getattr(self, "sensor", None))
        if getattr(self, "compact_cells", False):
            selected = cell_layout(values, start, int(self.trace.observer_probe["samples_per_cell"]))[3]
            paths_by_ray = {int(i): [tuple(p) for p in paths[i, :int(values[i, 3])]] for i in selected if i < count and values[i, 3] > 0}
            completed = compact_cell_batch(self.trace, values, start, view, plan["columns"], paths_by_ray)
        else:
            completed = []
            for i, row in enumerate(values):
                length = int(row[3])
                path = [tuple(p) for p in paths[i, :length]] if length else None
                completed.append((start+i, float(row[0]), path, int(row[2]), path[-1] if path else None, float(row[1]), float(row[7])))
        return completed, SimpleNamespace(cpu_fallback=True, reason=self.failure_reason,
            logical_intersection_rows=int(values[:, 5].sum()), total_sec=time.perf_counter()-began), int(values[:, 4].sum())

    def _run_gpu(self, plan, center, view, start, count):
        began = time.perf_counter()
        depth = self.trace.config.max_depth
        reused = self.workspace is not None and self.workspace[0] >= count
        if not reused:
            capacity = self.batch_size(8192)
            self.workspace = (capacity, self.cuda.device_array((capacity, depth+3, 3), dtype=np.float64),
                self.cuda.device_array((capacity, depth+3, 3), dtype=np.float64),
                self.cuda.device_array((capacity, 8), dtype=np.float64),
                self.cuda.device_array(capacity*self.scene.stack_width, dtype=np.float64),
                self.cuda.device_array(capacity*self.scene.stack_width, dtype=np.int64))
        _, vertices, paths, output, entries, nodes = self.workspace
        workspace_sec = time.perf_counter()-began
        upload_started = time.perf_counter()
        p = self.cuda.to_device(camera_plan(plan, center))
        sensor_rays = self.cuda.to_device(self.sensor.rays(self.trace.config.seed, start, count) if self.sensor is not None else np.empty((0, 7), dtype=np.float64))
        upload_sec = time.perf_counter()-upload_started
        d, c = self.device_scene, self.trace.config
        kernel_began = time.perf_counter()
        self.kernel[(count+127)//128, 128](start, count, view, int(c.seed)&0xffffffff,
            int(self.trace.observer_probe["samples_per_cell"]), plan["columns"], p, sensor_rays, max(c.epsilon_mm, 1e-8), depth,
            c.angle_dependent_reflectance, c.k_brdf, self.static["materials"], self.static["normals"], self.static["lights"], self.static["triangles"], self.static["quadrature"],
            self.static["face_light_offsets"], self.static["face_light_records"], self.static["virtual_light_offsets"], self.static["virtual_light_records"],
            self.static["virtual_node_bounds"], self.static["virtual_node_ranges"], self.static["virtual_light_trees"],
            self.scene.stack_width, d.triangle_v0, d.triangle_edge1, d.triangle_edge2, d.node_bounds_min, d.node_bounds_max,
            d.node_left, d.node_right, d.node_start, d.node_count, d.ordered_faces, d.traceable_face_mask, entries, nodes, vertices, paths, output)
        self.cuda.synchronize()
        kernel_sec = time.perf_counter()-kernel_began
        download_began = time.perf_counter()
        values = output[:count].copy_to_host()
        if (not np.all(np.isfinite(values)) or np.any(values[:, :2] < 0) or np.any(values[:, 6] != 0)
            or np.any(values[:, 2:7] != np.floor(values[:, 2:7])) or np.any(values[:, 2] < 0)
            or np.any(values[:, 2] > depth) or np.any(values[:, 3] < 0) or np.any(values[:, 3] > depth+3)
            or np.any(values[:, 4] < 0) or np.any(values[:, 4] > 1) or np.any(values[:, 5] < 0) or np.any(values[:,7] < 0)
            or np.any(values[:,7] > values[:,0]+1e-9*np.maximum(1.,values[:,0]))):
            raise RuntimeError("observer_resident_invalid_or_overflow_result")
        # Compact positive representative paths on the GPU, not the full
        # max-depth geometry tape for all zero-hit and shallow samples.
        positive = np.flatnonzero(values[:, 3] > 0)
        if self.compact_cells and self.validated:
            selected = cell_layout(values, start, int(self.trace.observer_probe["samples_per_cell"]))[3]
            positive = selected[selected < count]
        paths_by_ray = {}
        if len(positive):
            lengths = values[positive, 3].astype(np.int64)
            depth_slots = int(lengths.max())
            if self.compact_paths is None or self.compact_paths.shape[0] < len(positive) or self.compact_paths.shape[1] != depth_slots:
                self.compact_paths = self.cuda.device_array((len(positive), depth_slots, 3), dtype=np.float64)
            self.path_gather[(len(positive)+127)//128, 128](self.cuda.to_device(positive), self.cuda.to_device(lengths), paths, self.compact_paths)
            self.cuda.synchronize()
            host_paths = self.compact_paths[:len(positive)].copy_to_host()
            used = np.arange(depth_slots)[None, :] < lengths[:, None]
            if not np.all(np.isfinite(host_paths[used])):
                raise RuntimeError("observer_resident_nonfinite_path")
            for compact_index, i in enumerate(positive):
                n = int(values[i, 3])
                paths_by_ray[int(i)] = [tuple(p) for p in host_paths[compact_index, :n]]
        download_sec = time.perf_counter()-download_began
        if not self.validated:
            # Fail closed before the first commit: same scene and sample keys,
            # independent CPU geometry traversal. This is a runtime consistency
            # guard, separate from the analytic tests and real-device benchmark.
            n = min(count, 32)
            validation_started = time.perf_counter()
            reference, reference_paths = reference_chunk(self.trace, self.host, plan, center, view, start, n, sensor=self.sensor)
            if not np.array_equal(values[:n, 2:7], reference[:, 2:7]) or not np.allclose(values[:n, [0,1,7]], reference[:, [0,1,7]], rtol=1e-9, atol=1e-10):
                raise RuntimeError("observer_resident_first_chunk_parity_failed")
            for i in range(n):
                length = int(values[i, 3])
                if length and not np.allclose(paths_by_ray[i], reference_paths[i, :length], rtol=1e-9, atol=1e-8):
                    raise RuntimeError("observer_resident_first_chunk_path_parity_failed")
            self.validated = True
            self.validation_sec += time.perf_counter()-validation_started
        if self.compact_cells:
            completed = compact_cell_batch(self.trace, values, start, view, plan["columns"], paths_by_ray)
        else:
            completed = []
            for i, row in enumerate(values):
                path = paths_by_ray.get(i)
                completed.append((start+i, float(row[0]), path, int(row[2]), path[-1] if path else None, float(row[1]), float(row[7])))
        execution = SimpleNamespace(logical_intersection_rows=int(values[:, 5].sum()),
            active_ray_count_by_depth=[count], total_sec=time.perf_counter()-began,
            kernel_sec=kernel_sec, jit_compile_sec=0., numba_version=self.capability.numba_version,
            scene_upload_sec=0. if self.reused_scene else self.device_scene.upload_sec,
            workspace_prepare_sec=workspace_sec, input_upload_sec=upload_sec, output_download_sec=download_sec,
            device_name=self.capability.device_name, compute_capability=self.capability.compute_capability,
            device_id=self.capability.device_id, toolkit_layout=self.capability.toolkit_layout,
            reused_device_scene=self.reused_scene, reused_workspace=reused)
        self.reused_scene = True
        return completed, execution, int(values[:, 4].sum())
