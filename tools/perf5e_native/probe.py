from __future__ import annotations

import ctypes
import operator
from pathlib import Path
import threading
import time

import numpy as np


RAY_DTYPE = np.dtype([
    ("origin", "<f8", 3), ("direction", "<f8", 3),
    ("minimum", "<f8"), ("maximum", "<f8"), ("ignored_face", "<i8"),
])
HIT_DTYPE = np.dtype([
    ("distance", "<f8"), ("second_distance", "<f8"),
    ("face", "<i8"), ("flags", "<i8"),
    ("candidates", "<i8"), ("intersections", "<i8"),
])


def _stream_handle(value, allow_none=False):
    if value is None and allow_none:
        return 0
    if isinstance(value, (bool, np.bool_)):
        raise ValueError("CUDA stream must be an explicit nonzero handle")
    try:
        handle = operator.index(value)
    except TypeError as error:
        raise ValueError("CUDA stream must be an explicit nonzero handle") from error
    if handle <= 0 or handle > (1 << (8 * ctypes.sizeof(ctypes.c_void_p))) - 1:
        raise ValueError("CUDA stream must be an explicit nonzero handle")
    return handle


def _device_buffer(value, expected_dtype, writable=False):
    interface = getattr(value, "__cuda_array_interface__", None)
    if not isinstance(interface, dict) or type(interface.get("version")) is not int or interface["version"] != 3:
        raise ValueError("Device arrays must expose CUDA Array Interface version 3")
    shape = interface.get("shape")
    if not isinstance(shape, tuple) or len(shape) != 1 or type(shape[0]) is not int or not 0 <= shape[0] <= np.iinfo(np.uint32).max:
        raise ValueError("Device arrays must have a one-dimensional uint32-sized shape")
    try:
        description = interface.get("descr")
        if description is None:
            description = getattr(value, "dtype", None)
            if description is None:
                raise ValueError("A structured dtype is required")
        dtype = np.dtype(description)
        scalar_dtype = np.dtype(interface["typestr"])
    except (TypeError, ValueError, KeyError) as error:
        raise ValueError("Invalid device array dtype") from error
    if dtype != expected_dtype or scalar_dtype.itemsize != expected_dtype.itemsize or scalar_dtype.kind != "V":
        raise ValueError("Device array layout does not match the probe ABI")
    if interface.get("strides") not in (None, (expected_dtype.itemsize,)):
        raise ValueError("Device arrays must be contiguous")
    if interface.get("mask") is not None:
        raise ValueError("Masked device arrays are unsupported")
    data = interface.get("data")
    if not isinstance(data, tuple) or len(data) != 2 or type(data[0]) is not int or type(data[1]) is not bool:
        raise ValueError("Invalid device array data")
    pointer, readonly = data
    if pointer < 0 or pointer > np.iinfo(np.uint64).max or (shape[0] and (not pointer or pointer % 8)):
        raise ValueError("Device pointer must be nonnull and aligned")
    if writable and readonly:
        raise ValueError("Hit buffer must be writable")
    return pointer, shape[0], _stream_handle(interface.get("stream"), allow_none=True)


def pack_rays(origins, directions, minimum=1e-6, maximum=np.inf, ignored=-1):
    origins = np.asarray(origins, dtype=np.float64)
    directions = np.asarray(directions, dtype=np.float64)
    if origins.ndim != 2 or origins.shape[1:] != (3,) or directions.shape != origins.shape:
        raise ValueError("Origins and directions must have matching (N, 3) shapes")
    if not np.all(np.isfinite(origins)) or not np.all(np.isfinite(directions)):
        raise ValueError("Rays must be finite")
    rays = np.empty(len(origins), dtype=RAY_DTYPE)
    rays["origin"] = origins
    rays["direction"] = directions
    rays["minimum"] = minimum
    rays["maximum"] = maximum
    ignored_array = np.asarray(ignored)
    if ignored_array.dtype.kind not in "iu" or np.any(ignored_array < -1):
        raise ValueError("Ignored faces must be integer IDs or -1")
    rays["ignored_face"] = ignored_array
    if not np.array_equal(rays["ignored_face"], np.broadcast_to(ignored_array, (len(origins),))):
        raise ValueError("Ignored face IDs exceed int64")
    if np.any(~np.isfinite(rays["minimum"])) or np.any(rays["minimum"] < 0) or np.any(np.isnan(rays["maximum"])):
        raise ValueError("Invalid distance limits")
    return rays


class OptixCandidateProbe:
    contract = "experimental_candidates_unverified_v1"
    production_enabled = False

    def __init__(self, scene, directory: Path, device_ordinal=0):
        self._handle = None
        self._lock = threading.RLock()
        directory = Path(directory).resolve()
        self._library = ctypes.CDLL(str(directory / "optix_probe.dll"))
        self._library.probe_abi.restype = ctypes.c_int
        if self._library.probe_abi() != 2:
            raise RuntimeError("Unsupported probe ABI")
        coverage_abi = getattr(self._library, "probe_coverage_abi", None)
        self._coverage_query = None
        if coverage_abi is not None:
            coverage_abi.restype = ctypes.c_int
            if coverage_abi() != 1:
                raise RuntimeError("Unsupported coverage probe ABI")
            self._coverage_query = self._library.probe_query_coverage
            self._coverage_query.restype = ctypes.c_int
            self._coverage_query.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32] + [ctypes.c_void_p] * 4
        self._library.probe_error.restype = ctypes.c_char_p
        self._library.probe_create.restype = ctypes.c_void_p
        self._library.probe_create.argtypes = [ctypes.c_void_p] * 4 + [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_int]
        self._library.probe_query.restype = ctypes.c_int
        self._library.probe_query.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
        self._library.probe_query_device.restype = ctypes.c_int
        self._library.probe_query_device.argtypes = [
            ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint32, ctypes.c_uint64,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ]
        self._library.probe_destroy.argtypes = [ctypes.c_void_p]
        self._library.probe_destroy.restype = None
        self._library.probe_info.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._library.probe_info.restype = ctypes.c_int
        face_ids = np.flatnonzero(scene.traceable_face_mask).astype(np.int64)
        self._traceable_faces = np.asarray(scene.traceable_face_mask, dtype=np.bool_).copy()
        if not len(face_ids) or len(face_ids) > np.iinfo(np.uint32).max:
            raise ValueError("Probe requires 1 to uint32.max traceable faces")
        arrays = [np.ascontiguousarray(getattr(scene, name)[face_ids], dtype=np.float64)
                  for name in ("triangle_v0", "triangle_edge1", "triangle_edge2")]
        if any(values.shape != (len(face_ids), 3) or not np.all(np.isfinite(values)) for values in arrays):
            raise ValueError("Invalid triangle arrays")
        started = time.perf_counter()
        self._handle = self._library.probe_create(
            *(values.ctypes.data for values in arrays), face_ids.ctypes.data,
            len(face_ids), str(directory / "optix_probe.ptx").encode("utf-8"), device_ordinal,
        )
        if not self._handle:
            raise RuntimeError(self._library.probe_error().decode("utf-8", errors="replace"))
        self.build_seconds = time.perf_counter() - started
        metadata = np.zeros(6, dtype=np.float64)
        self._library.probe_info(self._handle, metadata.ctypes.data)
        self.metadata = {"center": metadata[:3].tolist(), "radius": float(metadata[3]),
                         "padding": float(metadata[4]), "acceleration_bytes": int(metadata[5])}

    def query(self, rays):
        rays = np.asarray(rays)
        if rays.dtype != RAY_DTYPE or rays.ndim != 1 or not rays.flags.c_contiguous:
            raise ValueError("Use pack_rays to construct contiguous probe inputs")
        if len(rays) > np.iinfo(np.uint32).max:
            raise ValueError("Probe launch exceeds uint32")
        with self._lock:
            if not self._handle:
                raise RuntimeError("Probe is closed")
            hits = np.empty(len(rays), dtype=HIT_DTYPE)
            milliseconds = np.zeros(3, dtype=np.float64)
            if len(rays) and self._library.probe_query(self._handle, rays.ctypes.data, len(rays), hits.ctypes.data, milliseconds.ctypes.data):
                raise RuntimeError(self._library.probe_error().decode("utf-8", errors="replace"))
            return hits, dict(zip(("upload_ms", "launch_sync_ms", "download_ms"), milliseconds.tolist()))

    def query_device(self, rays, hits, *, stream_handle):
        ray_pointer, ray_count, ray_stream = _device_buffer(rays, RAY_DTYPE)
        hit_pointer, hit_count, hit_stream = _device_buffer(hits, HIT_DTYPE, writable=True)
        execution_stream = _stream_handle(stream_handle)
        if ray_count != hit_count:
            raise ValueError("Device input and output counts must match")
        with self._lock:
            if not self._handle:
                raise RuntimeError("Probe is closed")
            milliseconds = np.zeros(2, dtype=np.float64)
            if ray_count and self._library.probe_query_device(
                self._handle, ray_pointer, ray_count, hit_pointer, ray_stream, hit_stream,
                execution_stream, milliseconds.ctypes.data,
            ):
                raise RuntimeError(self._library.probe_error().decode("utf-8", errors="replace"))
            return {"dependency_enqueue_ms": float(milliseconds[0]),
                    "launch_sync_ms": float(milliseconds[1]),
                    "ray_upload_bytes": 0, "hit_download_bytes": 0,
                    "execution": "synchronous_device_buffers_v1",
                    "production_enabled": False}

    def query_coverage(self, rays, expected_faces):
        rays = np.asarray(rays)
        if rays.dtype != RAY_DTYPE or rays.ndim != 1 or not rays.flags.c_contiguous:
            raise ValueError("Use pack_rays to construct contiguous probe inputs")
        if len(rays) > np.iinfo(np.uint32).max:
            raise ValueError("Probe launch exceeds uint32")
        expected_faces = np.asarray(expected_faces)
        if expected_faces.dtype != np.dtype("int64") or expected_faces.shape != (len(rays), 2):
            raise ValueError("Expected faces must be an int64 (N, 2) array")
        if np.any(expected_faces < -1) or np.any(expected_faces >= len(self._traceable_faces)):
            raise ValueError("Expected faces exceed the scene")
        present = expected_faces[expected_faces >= 0]
        if not np.all(self._traceable_faces[present]):
            raise ValueError("Expected faces must be traceable")
        expected_faces = np.ascontiguousarray(expected_faces)
        with self._lock:
            if not self._handle:
                raise RuntimeError("Probe is closed")
            if self._coverage_query is None:
                raise RuntimeError("Coverage queries require a coverage-enabled probe build")
            hits = np.empty(len(rays), dtype=HIT_DTYPE)
            visited = np.zeros(len(rays), dtype=np.uint8)
            milliseconds = np.zeros(3, dtype=np.float64)
            if len(rays) and self._coverage_query(
                self._handle, rays.ctypes.data, len(rays), expected_faces.ctypes.data,
                hits.ctypes.data, visited.ctypes.data, milliseconds.ctypes.data,
            ):
                raise RuntimeError(self._library.probe_error().decode("utf-8", errors="replace"))
            return hits, visited, dict(zip(("upload_ms", "launch_sync_ms", "download_ms"), milliseconds.tolist()))

    def close(self):
        with self._lock:
            if self._handle:
                self._library.probe_destroy(self._handle)
                self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()
