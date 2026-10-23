from __future__ import annotations

from dataclasses import dataclass
import math
import threading
import time

import numpy as np

from tools.perf5e_native.probe import HIT_DTYPE, RAY_DTYPE, OptixCandidateProbe, _device_buffer
from tools.perf5e_native.selective_replay import _winner_distance


CONTRACT = "experimental_device_selective_replay_shared_oracle_v2"
_KERNELS = None
STATUS_NAMES = ("invalid_rows", "replay_rows", "refinement_rejected_rows", "face_mismatches",
                "distance_bit_mismatches", "overflow_rows", "checked_rows")


def _kernels():
    global _KERNELS
    if _KERNELS is not None:
        return _KERNELS
    from numba import cuda
    from numba.cuda import libdevice

    refine = cuda.jit(device=True, inline=True, fastmath=False)(_winner_distance)

    @cuda.jit
    def reset(status):
        for field in range(len(status)):
            status[field] = 0

    @cuda.jit(fastmath=False)
    def classify(packed, candidate, origins, directions, minimum, maximum,
                 ignored, vertices, edges1, edges2, mask, refined, replay, invalid, rejected):
        row = cuda.grid(1)
        if row >= len(packed):
            return
        valid = math.isfinite(packed[row].minimum) and packed[row].minimum >= 0
        valid = valid and not math.isnan(packed[row].maximum) and packed[row].ignored_face >= -1
        for axis in range(3):
            origins[row, axis] = packed[row].origin[axis]
            directions[row, axis] = packed[row].direction[axis]
            valid = valid and math.isfinite(origins[row, axis]) and math.isfinite(directions[row, axis])
        minimum[row] = packed[row].minimum
        maximum[row] = packed[row].maximum
        ignored[row] = packed[row].ignored_face
        face = candidate[row].face
        flags = candidate[row].flags
        distance = candidate[row].distance
        valid = valid and 0 <= flags <= 7 and -1 <= face < len(mask)
        if 0 <= face < len(mask):
            valid = valid and mask[face] and face != ignored[row]
            valid = valid and math.isfinite(distance) and minimum[row] < distance <= maximum[row]
        elif face == -1:
            valid = valid and distance == math.inf
        invalid[row] = not valid
        replay[row] = flags != 0 or face < 0 or not valid
        rejected[row] = False
        refined[row] = math.inf
        if not replay[row]:
            refined[row] = refine(row, origins, directions, minimum, maximum, candidate.face,
                                  vertices, edges1, edges2)
            if not math.isfinite(refined[row]) or not minimum[row] < refined[row] <= maximum[row]:
                replay[row] = True
                rejected[row] = True
        if not valid:
            maximum[row] = minimum[row]

    @cuda.jit(fastmath=False)
    def certify(count, candidate, refined, replay, invalid, rejected, distances, faces, overflow, status):
        row = cuda.grid(1)
        if row >= count:
            return
        if not replay[row]:
            if candidate[row].face != faces[row]:
                cuda.atomic.add(status, 3, 1)
            if libdevice.double_as_longlong(refined[row]) != libdevice.double_as_longlong(distances[row]):
                cuda.atomic.add(status, 4, 1)
            distances[row] = refined[row]
            faces[row] = candidate[row].face
        if invalid[row]:
            cuda.atomic.add(status, 0, 1)
        if replay[row]:
            cuda.atomic.add(status, 1, 1)
        if rejected[row]:
            cuda.atomic.add(status, 2, 1)
        if overflow[row]:
            cuda.atomic.add(status, 5, 1)
        cuda.atomic.add(status, 6, 1)

    _KERNELS = reset, classify, certify
    return _KERNELS


def check_status(status, count):
    if status.dtype != np.int64 or status.shape != (len(STATUS_NAMES),):
        raise RuntimeError("Invalid device verification status layout")
    values = dict(zip(STATUS_NAMES, map(int, status)))
    if any(value < 0 or value > count for value in values.values()):
        raise RuntimeError("Invalid device verification counts")
    if values["checked_rows"] != count or values["refinement_rejected_rows"] > values["replay_rows"]:
        raise RuntimeError("Incomplete device verification")
    if any(values[name] for name in ("invalid_rows", "face_mismatches", "distance_bit_mismatches", "overflow_rows")):
        raise RuntimeError(f"Device selective replay failed closed: {values}")
    return values


@dataclass(frozen=True)
class DeviceQueryResult:
    distances: object
    face_indices: object
    replay: object
    candidate: object
    metadata: dict


class DeviceSelectiveReplay:
    """Isolated, synchronous experiment; result views expire on the next query or close."""

    contract = CONTRACT
    production_enabled = False

    def __init__(self, mesh, directory, capacity):
        if type(capacity) is not int or not 1 <= capacity <= np.iinfo(np.uint32).max:
            raise ValueError("Capacity must be a positive uint32 integer")
        from numba import cuda
        from leakage_simulator import gpu_cuda_intersection as gpu

        self._owner = threading.get_ident()
        self._context = cuda.current_context().handle.value
        self._closed = False
        self._lock = threading.Lock()
        self.capacity = capacity
        self.stream = cuda.stream()
        self._cuda = cuda
        self._scene = mesh.prepare_gpu_cuda_scene()
        self._device, _ = gpu._ensure_device_scene(self._scene, gpu.probe_gpu_cuda())
        self._bvh = gpu._ensure_kernel()
        self._reset, self._classify, self._certify = _kernels()
        self._arrays = {}
        specifications = {
            "candidate": (capacity, HIT_DTYPE),
            "origins": ((capacity, 3), np.float64), "directions": ((capacity, 3), np.float64),
            **{name: (capacity, np.float64) for name in (
                "minimum", "maximum", "refined", "distances")},
            **{name: (capacity, np.int64) for name in ("ignored", "faces")},
            **{name: (capacity, np.uint8) for name in ("replay", "invalid", "rejected", "overflow")},
            "stack_entries": (capacity * self._scene.stack_width, np.float64),
            "stack_nodes": (capacity * self._scene.stack_width, np.int32),
            "status": (len(STATUS_NAMES), np.int64),
        }
        for name, (shape, dtype) in specifications.items():
            self._arrays[name] = cuda.device_array(shape, dtype=dtype, stream=self.stream)
        self.workspace_bytes = sum(array.nbytes for array in self._arrays.values())
        self._probe = OptixCandidateProbe(mesh.prepare_native_cpu_scene(), directory)

    def _check_owner(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError("Device replay is thread-affine")
        if self._cuda.current_context().handle.value != self._context:
            raise RuntimeError("Device replay context changed")
        if self._closed:
            raise RuntimeError("Device replay is closed")

    def _launch_bvh(self, count, maximum, distances, faces, overflow):
        arrays = self._arrays
        device = self._device
        self._bvh[(count + 127) // 128, 128, self.stream](
            count, self._scene.stack_width, arrays["origins"], arrays["directions"], arrays["minimum"],
            maximum, arrays["ignored"], device.triangle_v0, device.triangle_edge1, device.triangle_edge2,
            device.node_bounds_min, device.node_bounds_max, device.node_left, device.node_right,
            device.node_start, device.node_count, device.ordered_faces, device.traceable_face_mask,
            distances, faces, arrays["stack_entries"], arrays["stack_nodes"], overflow,
        )

    def query_device(self, packed):
        from numba.cuda.cudadrv.devicearray import DeviceNDArray

        self._check_owner()
        _, count, _ = _device_buffer(packed, RAY_DTYPE)
        if not isinstance(packed, DeviceNDArray) or packed.dtype != RAY_DTYPE:
            raise ValueError("This experimental adapter requires a typed Numba device ray array")
        if count > self.capacity:
            raise ValueError("Ray batch exceeds workspace capacity")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("Concurrent device replay is unsupported")
        try:
            started = time.perf_counter()
            arrays = self._arrays
            candidate = arrays["candidate"][:count]
            timings = self._probe.query_device(packed, candidate, stream_handle=self.stream.handle.value)
            rays = packed
            self._reset[1, 1, self.stream](arrays["status"])
            if count:
                device = self._device
                blocks = (count + 127) // 128
                self._classify[blocks, 128, self.stream](
                    rays, candidate, arrays["origins"], arrays["directions"], arrays["minimum"], arrays["maximum"],
                    arrays["ignored"], device.triangle_v0, device.triangle_edge1,
                    device.triangle_edge2, device.traceable_face_mask, arrays["refined"], arrays["replay"],
                    arrays["invalid"], arrays["rejected"],
                )
                self._launch_bvh(count, arrays["maximum"], arrays["distances"], arrays["faces"], arrays["overflow"])
                self._certify[blocks, 128, self.stream](
                    count, candidate, arrays["refined"], arrays["replay"], arrays["invalid"], arrays["rejected"],
                    arrays["distances"], arrays["faces"], arrays["overflow"], arrays["status"],
                )
            status = arrays["status"].copy_to_host(stream=self.stream)
            self.stream.synchronize()
            counts = check_status(status, count)
            metadata = {
                **counts, "contract": CONTRACT, "production_enabled": False, "production_coverage_proven": False,
                "full_oracle_rows": count, "winner_only_rows": count - counts["replay_rows"],
                "canonical_query_rows": count, "canonical_launches": int(count > 0),
                "duplicate_replay_rows_avoided": counts["replay_rows"],
                "workspace_bytes_saved_vs_v1": 25 * self.capacity,
                "oracle_strategy": "one_full_canonical_pass_shared_with_replay",
                "ray_upload_bytes": 0, "hit_download_bytes": 0, "status_download_bytes": status.nbytes,
                "workspace_bytes": self.workspace_bytes, "query_seconds": time.perf_counter() - started,
                "performance_acceptance_eligible": False, "optix_timings": timings,
                "result_lifetime": "borrowed_until_next_query_or_close",
            }
            return DeviceQueryResult(arrays["distances"][:count], arrays["faces"][:count],
                                     arrays["replay"][:count], candidate, metadata)
        except BaseException:
            self.stream.synchronize()
            raise
        finally:
            self._lock.release()

    def close(self):
        if self._closed:
            return
        self._check_owner()
        with self._lock:
            self.stream.synchronize()
            self._probe.close()
            self._arrays.clear()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()
