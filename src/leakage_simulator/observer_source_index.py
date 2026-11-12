"""Conservative ordered AABB hierarchy for transparent emitting triangles.

Leaves retain input order: emission accumulation and shared-edge ownership keep
the same semantics as the exhaustive loop. Bounds reject candidates only; exact
triangle/emission/occlusion tests still decide every contribution.
"""
import numpy as np


def ordered_triangle_bounds(triangles, leaf_size=8):
    vertices = np.asarray(triangles, dtype=float).reshape(-1, 3, 3)
    if not len(vertices):
        return np.empty((0, 6), dtype=float), np.empty((0, 3), dtype=np.int64)
    lows, highs = vertices.min(axis=1), vertices.max(axis=1)
    # Include the exact triangle test's barycentric boundary tolerance.
    padding = 1e-9 + (highs-lows).max(axis=1)*1e-9
    lows, highs = lows-padding[:, None], highs+padding[:, None]
    bounds, ranges = [], []

    def build(start, end):
        node = len(bounds)
        bounds.append([*lows[start:end].min(axis=0), *highs[start:end].max(axis=0)])
        ranges.append([-1, -1, -1])
        if end-start <= leaf_size:
            ranges[node][0:2] = [start, end]
        else:
            middle = (start+end)//2
            build(start, middle)
            build(middle, end)
        ranges[node][2] = len(bounds)
    build(0, len(vertices))
    return np.asarray(bounds, dtype=float), np.asarray(ranges, dtype=np.int64)


class VirtualSourceIndex:
    def __init__(self, triangles):
        self.bounds, self.ranges = ordered_triangle_bounds(triangles)

    def candidates(self, origin, direction, epsilon, maximum):
        node = 0
        while node < len(self.bounds):
            box = self.bounds[node]
            enter, leave = epsilon, maximum
            for axis in range(3):
                if direction[axis] == 0:
                    if origin[axis] < box[axis] or origin[axis] > box[axis+3]:
                        leave = -float('inf')
                        break
                else:
                    a = (box[axis]-origin[axis])/direction[axis]
                    b = (box[axis+3]-origin[axis])/direction[axis]
                    enter, leave = max(enter, min(a,b)), min(leave, max(a,b))
            start, end, skip = self.ranges[node]
            if enter > leave:
                node = int(skip)
            elif start >= 0:
                yield from range(int(start), int(end))
                node = int(skip)
            else:
                node += 1
