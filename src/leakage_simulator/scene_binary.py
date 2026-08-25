from __future__ import annotations

import json
import struct
import sys
from array import array
from collections.abc import Iterable, Iterator, Sequence
from itertools import chain
from typing import Any


MAGIC = b"BITSAMSC"
VERSION = 1
HEADER = struct.Struct("<8sII")
MEDIA_TYPE = "application/vnd.bitsam.scene-binary"
_CHUNK_VALUES = 262_144


def _flatten(rows: Iterable[Sequence[Any]]) -> Iterator[Any]:
    for row in rows:
        yield from row


def _material_codes(values: Sequence[Any]) -> tuple[list[str], array[int]]:
    table: list[str] = []
    lookup: dict[str, int] = {}
    codes = array("I")
    for raw_value in values:
        value = str(raw_value or "")
        code = lookup.get(value)
        if code is None:
            code = len(table)
            lookup[value] = code
            table.append(value)
        codes.append(code)
    return table, codes


def _component_faces(
    components: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], Iterator[int]]:
    manifest_components: list[dict[str, Any]] = []
    offset = 0
    for component in components:
        face_indices = component.get("face_indices") or []
        item = {key: value for key, value in component.items() if key != "face_indices"}
        item["binary_face_offset"] = offset
        item["binary_face_count"] = len(face_indices)
        manifest_components.append(item)
        offset += len(face_indices)
    return manifest_components, (
        int(value)
        for value in chain.from_iterable(
            component.get("face_indices") or [] for component in components
        )
    )


def prepare_scene_binary(
    payload: dict[str, Any],
) -> tuple[bytes, list[tuple[str, str, int, Iterable[Any]]]]:
    """Build a small JSON manifest plus native numeric array descriptors.

    Geometry values are streamed later, so this function never creates the
    very large JSON body that used to be parsed by the browser.
    """

    mesh = payload.get("mesh")
    if not isinstance(mesh, dict):
        raise ValueError("Scene payload is missing mesh data")
    components = payload.get("components")
    if not isinstance(components, list):
        raise ValueError("Scene payload components must be an array")

    manifest_components, component_face_values = _component_faces(components)
    material_table, material_values = _material_codes(mesh.get("face_material_ids") or [])
    edges = mesh.get("feature_edge_segments") or []

    blocks: list[tuple[str, str, int, Iterable[Any]]] = [
        ("vertices", "float64", 3, _flatten(mesh.get("vertices") or [])),
        ("faces", "uint32", 3, _flatten(mesh.get("faces") or [])),
        ("face_ids", "uint32", 1, iter(mesh.get("face_ids") or [])),
        (
            "face_component_ids",
            "int32",
            1,
            (int(value) if value is not None else -1 for value in (mesh.get("face_component_ids") or [])),
        ),
        ("face_material_codes", "uint32", 1, material_values),
        ("face_source_ids", "uint32", 1, iter(mesh.get("face_source_ids") or [])),
        ("face_normals", "float64", 3, _flatten(mesh.get("face_normals") or [])),
        ("face_centroids", "float64", 3, _flatten(mesh.get("face_centroids") or [])),
        ("face_areas_mm2", "float64", 1, iter(mesh.get("face_areas_mm2") or [])),
        (
            "feature_edge_points",
            "float64",
            6,
            (
                coordinate
                for edge in edges
                for coordinate in (*edge["start"], *edge["end"])
            ),
        ),
        (
            "feature_edge_component_ids",
            "int32",
            1,
            (int(edge["component_id"]) if edge.get("component_id") is not None else -1 for edge in edges),
        ),
        ("component_face_indices", "uint32", 1, component_face_values),
    ]

    dtype_sizes = {"float64": 8, "uint32": 4, "int32": 4}
    counts = {
        "vertices": len(mesh.get("vertices") or []),
        "faces": len(mesh.get("faces") or []),
        "face_ids": len(mesh.get("face_ids") or []),
        "face_component_ids": len(mesh.get("face_component_ids") or []),
        "face_material_codes": len(mesh.get("face_material_ids") or []),
        "face_source_ids": len(mesh.get("face_source_ids") or []),
        "face_normals": len(mesh.get("face_normals") or []),
        "face_centroids": len(mesh.get("face_centroids") or []),
        "face_areas_mm2": len(mesh.get("face_areas_mm2") or []),
        "feature_edge_points": len(edges),
        "feature_edge_component_ids": len(edges),
        "component_face_indices": sum(item["binary_face_count"] for item in manifest_components),
    }
    offset = 0
    descriptors: dict[str, dict[str, Any]] = {}
    for name, dtype, width, _values in blocks:
        offset = (offset + 7) & ~7
        byte_length = counts[name] * width * dtype_sizes[dtype]
        descriptors[name] = {
            "dtype": dtype,
            "width": width,
            "count": counts[name],
            "byte_offset": offset,
            "byte_length": byte_length,
        }
        offset += byte_length

    manifest = {
        "schema_version": "mesh-scene.v2-binary",
        "units": payload.get("units"),
        "coordinate_system": payload.get("coordinate_system"),
        "components": manifest_components,
        "metadata": payload.get("metadata"),
        "binary": {
            "version": VERSION,
            "byte_order": "little",
            "byte_length": offset,
            "arrays": descriptors,
            "face_material_table": material_table,
        },
    }
    manifest_bytes = json.dumps(
        manifest,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    # TypedArray constructors require naturally aligned byte offsets. The
    # fixed header is 16 bytes, so pad JSON with legal trailing whitespace.
    manifest_bytes += b" " * ((-len(manifest_bytes)) % 8)
    return manifest_bytes, blocks


def iter_scene_binary(
    manifest: bytes,
    blocks: Sequence[tuple[str, str, int, Iterable[Any]]],
) -> Iterator[bytes]:
    yield HEADER.pack(MAGIC, VERSION, len(manifest))
    yield manifest
    typecodes = {"float64": "d", "uint32": "I", "int32": "i"}
    expected_sizes = {"float64": 8, "uint32": 4, "int32": 4}
    emitted_data_bytes = 0
    for _name, dtype, _width, values in blocks:
        padding = (-emitted_data_bytes) % 8
        if padding:
            yield b"\0" * padding
            emitted_data_bytes += padding
        typecode = typecodes[dtype]
        chunk: list[Any] = []
        for value in values:
            chunk.append(value)
            if len(chunk) >= _CHUNK_VALUES:
                packed = array(typecode, chunk)
                if packed.itemsize != expected_sizes[dtype]:
                    raise RuntimeError("Unsupported native array item size")
                if sys.byteorder != "little":
                    packed.byteswap()
                encoded = packed.tobytes()
                yield encoded
                emitted_data_bytes += len(encoded)
                chunk.clear()
        if chunk:
            packed = array(typecode, chunk)
            if packed.itemsize != expected_sizes[dtype]:
                raise RuntimeError("Unsupported native array item size")
            if sys.byteorder != "little":
                packed.byteswap()
            encoded = packed.tobytes()
            yield encoded
            emitted_data_bytes += len(encoded)
