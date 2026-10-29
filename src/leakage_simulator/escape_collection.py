"""Final unbounded-ray escape collection, independent of finite detector size."""
from __future__ import annotations
import math

DIRECTIONS = {"pos_x": (1, 0, 0), "neg_x": (-1, 0, 0), "pos_y": (0, 1, 0),
              "neg_y": (0, -1, 0), "pos_z": (0, 0, 1), "neg_z": (0, 0, -1)}


class EscapeCollector:
    def __init__(self, spec, receivers, grids, epsilon_mm=1e-4):
        if not isinstance(spec, dict) or spec.get("direction") not in DIRECTIONS:
            raise ValueError("Invalid escape collection direction")
        self.minimum, self.maximum = spec.get("minimum"), spec.get("maximum")
        if any(not isinstance(v, (list, tuple)) or len(v) != 3 or any(not math.isfinite(float(x)) for x in v)
               for v in (self.minimum, self.maximum)):
            raise ValueError("Escape bounds must be finite 3D coordinates")
        self.minimum = [float(v) for v in self.minimum]
        self.maximum = [float(v) for v in self.maximum]
        if any(self.maximum[i] <= self.minimum[i] for i in range(3)):
            raise ValueError("Escape bounds must have positive extent")
        self.tolerance = max(float(epsilon_mm) * 2, 1e-9)
        self.axis = DIRECTIONS[spec["direction"]]
        self.direction = spec["direction"]
        self.regions = spec.get("allowed_regions", [])
        if not isinstance(self.regions, list) or len(self.regions) > 10000:
            raise ValueError("Invalid escape allowed regions")
        for region in self.regions:
            if not isinstance(region, dict) or region.get("plane") not in ("xy", "yz", "zx", "xyz"):
                raise ValueError("Invalid escape allowed region")
            for axis in "xyz":
                for suffix in ("Min", "Max"):
                    value = region.get(axis + suffix)
                    if value is not None and not math.isfinite(float(value)):
                        raise ValueError("Escape allowed region coordinates must be finite")
        self.receivers = {r.receiver_id: r for r in receivers if r.enabled}
        self.grids = grids
        expected_ids = {"__leak_preview_" + face for face in DIRECTIONS}
        if set(self.receivers) != expected_ids or set(grids) != expected_ids:
            raise ValueError("Escape collection requires six enclosure grids")
        self.cells = {}
        self.bins = [{"min_deg": lo, "max_deg": hi, "count": 0, "flux_lumen": 0.0}
                     for lo, hi in ((0, 30), (30, 60), (60, 80), (80, 90))]
        self.escaped_count = self.excluded_count = self.selected_count = 0
        self.total_flux = self.excluded_flux = 0.0

    def _exit(self, origin, direction):
        if all(self.minimum[axis] - self.tolerance <= origin[axis] <= self.maximum[axis] + self.tolerance
               for axis in range(3)):
            origin = [max(self.minimum[axis], min(self.maximum[axis], origin[axis])) for axis in range(3)]
        enter, leave = -math.inf, math.inf
        face_axis, face_sign = 0, 1
        for i in range(3):
            if abs(direction[i]) < 1e-15:
                if origin[i] < self.minimum[i] - self.tolerance or origin[i] > self.maximum[i] + self.tolerance:
                    return None
                continue
            a = (self.minimum[i] - origin[i]) / direction[i]
            b = (self.maximum[i] - origin[i]) / direction[i]
            near, far = min(a, b), max(a, b)
            enter = max(enter, near)
            if far < leave:
                leave, face_axis, face_sign = far, i, 1 if direction[i] > 0 else -1
        if leave < max(enter, 0.0) - self.tolerance or not math.isfinite(leave):
            return None
        point = [max(self.minimum[i], min(self.maximum[i], origin[i] + leave * direction[i])) for i in range(3)]
        face = ("pos_" if face_sign > 0 else "neg_") + "xyz"[face_axis]
        return point, face

    def _allowed(self, origin, direction, exit_point):
        # Test the final, unobstructed ray segment against the actual Allowed
        # boundary. Testing only the enclosure exit displaces oblique holes.
        distance = sum((exit_point[i] - origin[i]) * direction[i] for i in range(3))
        for region in self.regions:
            axes = {"xy": (0, 1), "yz": (1, 2), "zx": (2, 0), "xyz": (0, 1, 2)}[region["plane"]]
            def inside(point):
                return all((region.get("xyz"[i] + "Min", -math.inf) if region.get("xyz"[i] + "Min") is not None else -math.inf) - self.tolerance <= point[i] <=
                           (region.get("xyz"[i] + "Max", math.inf) if region.get("xyz"[i] + "Max") is not None else math.inf) + self.tolerance for i in axes)
            if region["plane"] == "xyz":
                enter, leave = 0., max(0., distance)
                for i in range(3):
                    lo, hi = region.get("xyz"[i] + "Min"), region.get("xyz"[i] + "Max")
                    lo = -math.inf if lo is None else lo
                    hi = math.inf if hi is None else hi
                    if abs(direction[i]) < 1e-15:
                        if not lo - self.tolerance <= origin[i] <= hi + self.tolerance:
                            leave = -math.inf
                            break
                    else:
                        a, b = (lo - origin[i]) / direction[i], (hi - origin[i]) / direction[i]
                        enter, leave = max(enter, min(a, b)), min(leave, max(a, b))
                if leave >= enter - self.tolerance:
                    return True
                continue
            normal = next(i for i in range(3) if i not in axes)
            if abs(direction[normal]) < 1e-15:
                continue
            for suffix, fallback in (("Min", self.minimum[normal]), ("Max", self.maximum[normal])):
                coordinate = region.get("xyz"[normal] + suffix)
                if coordinate is None:
                    coordinate = fallback
                t = (coordinate - origin[normal]) / direction[normal]
                if -self.tolerance <= t <= distance + self.tolerance:
                    point = [origin[i] + t * direction[i] for i in range(3)]
                    if inside(point):
                        return True
        return False

    def record(self, origin, direction, power, depth):
        crossing = self._exit(origin, direction)
        if crossing is None or power <= 0:
            return False
        point, face = crossing
        self.escaped_count += 1
        self.total_flux += power
        cosine = sum(direction[i] * self.axis[i] for i in range(3))
        if cosine < -1e-12:
            return False
        if self._allowed(origin, direction, point):
            self.excluded_count += 1
            self.excluded_flux += power
            return False
        angle = round(math.degrees(math.acos(max(0.0, min(1.0, cosine)))), 10)
        index = 0 if angle < 30 else 1 if angle < 60 else 2 if angle < 80 else 3
        self.bins[index]["count"] += 1
        self.bins[index]["flux_lumen"] += power
        self.selected_count += 1
        receiver_id = "__leak_preview_" + face
        receiver, grid = self.receivers.get(receiver_id), self.grids.get(receiver_id)
        if receiver is None or grid is None:
            raise ValueError("Escape collection requires all six enclosure grids")
        relative = [point[i] - receiver.center[i] for i in range(3)]
        u = sum(relative[i] * receiver.u_axis[i] for i in range(3))
        v = sum(relative[i] * receiver.v_axis[i] for i in range(3))
        column = max(0, min(grid.resolution[0] - 1, int((u / receiver.width_mm + 0.5) * grid.resolution[0])))
        row = max(0, min(grid.resolution[1] - 1, int((v / receiver.height_mm + 0.5) * grid.resolution[1])))
        grid.flux_lumen[row][column] += power
        grid.hit_count += 1
        grid.flux_squared_lumen2 += power * power
        grid.flux_squared_lumen2_grid[row][column] += power * power
        key = (receiver_id, row, column)
        cell = self.cells.setdefault(key, {"receiver_id": receiver_id, "row": row, "column": column,
            "position_sum": [0., 0., 0.], "flux_lumen": 0., "count": 0, "grazing_count": 0,
            "min_depth": depth, "max_depth": depth, "angle_sum_deg": 0.})
        for i in range(3):
            cell["position_sum"][i] += point[i] * power
        cell["flux_lumen"] += power
        cell["count"] += 1
        cell["grazing_count"] += int(angle >= 60)
        cell["min_depth"] = min(cell["min_depth"], depth)
        cell["max_depth"] = max(cell["max_depth"], depth)
        cell["angle_sum_deg"] += angle
        return True

    def summary(self):
        cells = []
        for cell in self.cells.values():
            cells.append({k: v for k, v in cell.items() if k != "position_sum"} | {
                "position": [v / cell["flux_lumen"] for v in cell["position_sum"]]})
        return {"contract": "final_escape_directions_v1", "direction": self.direction,
                "escaped_count": self.escaped_count, "escaped_flux_lumen": self.total_flux,
                "selected_count": self.selected_count, "excluded_count": self.excluded_count,
                "excluded_flux_lumen": self.excluded_flux, "bins": self.bins, "cells": cells}
