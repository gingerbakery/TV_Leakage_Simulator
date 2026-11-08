"""Conservative bounded planar CAD-opening attribution for display only.

Never equate an AABB crossing or an illuminated surface with an opening.
Unsupported/non-manifold/open outlines stay unresolved. Optical evidence and
its weights are unchanged. The selected exit is the last recognized opening
on the audited surface-to-observer segment, not a proof of shell topology.
"""
import time
from collections import Counter, defaultdict

from .geometry import vec_sub, vec_dot, vec_cross, vec_norm

TOL = 1e-5


def inside(point, loop):
    x, y = point
    result = False
    for a, b in zip(loop, loop[1:]+loop[:1]):
        cross = (b[0]-a[0])*(y-a[1])-(b[1]-a[1])*(x-a[0])
        if abs(cross) <= TOL and min(a[0], b[0])-TOL <= x <= max(a[0], b[0])+TOL and min(a[1], b[1])-TOL <= y <= max(a[1], b[1])+TOL:
            return None  # Rim/grazing is ambiguous, not an aperture interior.
        if (a[1] > y) != (b[1] > y) and x < a[0]+(y-a[1])*(b[0]-a[0])/(b[1]-a[1]):
            result = not result
    return result


def planar_openings(mesh, deadline):
    planes = {}
    for face in range(len(mesh.faces)):
        if face % 1024 == 0 and time.perf_counter() > deadline:
            return None
        if mesh.metadata(face).get("preview_blocker_id"):
            continue  # Blocks can occlude, never define a new hole.
        triangle = mesh.face_vertices(face)
        raw = vec_cross(vec_sub(triangle[1], triangle[0]), vec_sub(triangle[2], triangle[0]))
        if vec_dot(raw, raw) < 1e-20:
            continue
        normal = vec_norm(raw)
        if normal[max(range(3), key=lambda i: abs(normal[i]))] < 0:
            normal = tuple(-v for v in normal)
        offset = vec_dot(normal, triangle[0])
        key = (*[round(v, 7) for v in normal], round(offset, 5))
        plane = planes.setdefault(key, dict(normal=normal, offset=offset, edges=Counter(), points={}))
        # Quantization stitches independently tessellated triangles. A rounded
        # plane bucket is still checked against its actual plane.
        if any(abs(vec_dot(plane["normal"], p)-plane["offset"]) > TOL for p in triangle):
            continue
        keys = [tuple(round(v/TOL) for v in point) for point in triangle]
        plane["points"].update(zip(keys, triangle))
        for a, b in zip(keys, keys[1:]+keys[:1]):
            plane["edges"][tuple(sorted((a, b)))] += 1
    result = []
    for plane_id, plane in enumerate(planes.values()):
        if time.perf_counter() > deadline:
            return None
        if any(count > 2 for count in plane["edges"].values()):
            continue
        links = defaultdict(set)
        for (a, b), count in plane["edges"].items():
            if count == 1:
                links[a].add(b); links[b].add(a)
        if not links or any(len(neighbors) != 2 for neighbors in links.values()):
            continue
        drop = max(range(3), key=lambda i: abs(plane["normal"][i]))
        axes = [i for i in range(3) if i != drop]
        unseen, loops = set(links), []
        while unseen:
            start = min(unseen)
            previous, current, loop = None, start, []
            while current in unseen:
                unseen.remove(current)
                point = plane["points"][current]
                loop.append([point[axes[0]], point[axes[1]]])
                nxt = next(node for node in sorted(links[current]) if node != previous)
                previous, current = current, nxt
            if current != start or len(loop) < 3:
                loops = []; break
            loops.append(loop)
        # A single outer contour bounds a solid sheet, not a hole.
        if len(loops) > 1:
            result.append(dict(id=f"plane-{plane_id}", normal=plane["normal"], offset=plane["offset"], axes=axes, loops=loops))
    return result


def attribute_observer_apertures(mesh, summary, budget_sec=5.):
    started = time.perf_counter()
    planes = planar_openings(mesh, started+budget_sec)
    confirmed = 0
    budget_exceeded = planes is None
    for cell in summary.get("cells", []):
        cell.pop("aperture", None)
        if time.perf_counter() > started+budget_sec:
            budget_exceeded = True
            continue
        audit, path = cell.get("path_validation", {}), cell.get("representative_path")
        if planes is None or audit.get("status") != "valid" or not path or len(path) < 2:
            continue
        start, end = path[-2:]
        delta, best = vec_sub(end, start), None
        for plane in planes:
            denominator = vec_dot(plane["normal"], delta)
            if abs(denominator) < 1e-12:
                continue
            t = (plane["offset"]-vec_dot(plane["normal"], start))/denominator
            if not 1e-7 < t < 1-1e-7:
                continue
            position = [start[i]+t*delta[i] for i in range(3)]
            projected = [position[i] for i in plane["axes"]]
            contained = [inside(projected, loop) for loop in plane["loops"]]
            if None in contained or sum(contained) == 0 or sum(contained) % 2 != 0:
                continue
            # Distinct holes on the same plane must not collapse together.
            memberships = [i for i, value in enumerate(contained) if value]
            identity = plane["id"]+":"+",".join(map(str, memberships))
            if best is None or t > best[0]:
                best = (t, identity, position)
        if best:
            cell["aperture"] = dict(contract="bounded_planar_opening_v1", id=best[1], position=best[2],
                scope="last_recognized_opening_on_audited_observer_segment", exterior_shell_certified=False)
            confirmed += 1
    summary["aperture_summary"] = dict(contract="bounded_planar_opening_v1", attributed=confirmed,
        unresolved=len(summary.get("cells", []))-confirmed,
        reason="geometry_budget_exceeded" if budget_exceeded else "bounded_planar_openings_only",
        runtime_sec=time.perf_counter()-started)
