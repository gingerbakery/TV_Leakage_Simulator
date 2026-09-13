from __future__ import annotations

import hashlib
import json
import math
import sys
import zipfile
from array import array
from collections.abc import Sequence
from itertools import repeat
from pathlib import Path
from typing import Any, Iterable

from .scene_binary import HEADER, MAGIC, VERSION, iter_scene_binary, prepare_scene_binary


PACKAGE_SCHEMA = "bitsam-project.v2"
CACHE_CONTRACT = "bitsam-lossless-scene.v1"
MEDIA_TYPE = "application/vnd.bitsam+zip"
MAX_PACKAGE_BYTES = 8 * 1024**3
MAX_JSON_BYTES = 128 * 1024**2
CHUNK_BYTES = 1024**2
SOURCE_SUFFIXES = {".stp", ".step", ".obj", ".stl"}


class NumericRows(Sequence):
    def __init__(self, values: array, width: int) -> None:
        self.values = values
        self.width = width

    def __len__(self) -> int:
        return len(self.values) // self.width

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[item] for item in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)
        offset = index * self.width
        return tuple(self.values[offset:offset + self.width])


def file_chunks(path: Path) -> Iterable[bytes]:
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_BYTES):
            yield chunk


def json_chunks(value: Any) -> Iterable[bytes]:
    encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    pending = []
    length = 0
    for fragment in encoder.iterencode(value):
        pending.append(fragment)
        length += len(fragment)
        if length >= CHUNK_BYTES:
            yield "".join(pending).encode("utf-8")
            pending = []
            length = 0
    if pending:
        yield "".join(pending).encode("utf-8")


def read_json(path: Path, limit: int = MAX_JSON_BYTES) -> dict[str, Any]:
    if path.stat().st_size > limit:
        raise ValueError("BITSAM JSON metadata exceeds the size limit")
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("BITSAM metadata must be an object")
    return value


def mesh_identity(mesh: dict[str, Any]) -> str:
    payload = {"mesh": mesh, "components": []}
    manifest, blocks = prepare_scene_binary(payload, coordinate_dtype="float64")
    digest = hashlib.sha256()
    for chunk in iter_scene_binary(manifest, blocks):
        digest.update(chunk)
    return digest.hexdigest()


def write_package(
    destination: Path,
    project: dict[str, Any],
    source: Path,
    scene: dict[str, Any],
    trace_mesh: dict[str, Any] | None,
) -> dict[str, Any]:
    if project.get("schema_version") != "bitsam-project.v1":
        raise ValueError("Unsupported BITSAM settings version")
    if not isinstance(project.get("workspace"), dict):
        raise ValueError("BITSAM workspace is missing")
    if source.suffix.lower() not in SOURCE_SUFFIXES or not source.is_file():
        raise ValueError("Original CAD is unavailable. Import the CAD once before saving.")
    source_entry = "cad/original" + source.suffix.lower()
    entries: dict[str, Any] = {}

    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        def write_entry(name: str, chunks: Iterable[bytes]) -> None:
            digest = hashlib.sha256()
            size = 0
            with archive.open(name, "w", force_zip64=True) as output:
                for chunk in chunks:
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                    if size > (MAX_JSON_BYTES if name == "project.json" else MAX_PACKAGE_BYTES):
                        raise ValueError("BITSAM entry exceeds the size limit")
            entries[name] = {"size": size, "sha256": digest.hexdigest()}

        write_entry(source_entry, file_chunks(source))
        write_entry("project.json", json_chunks(project))
        portable_scene = dict(scene)
        metadata = dict(scene["metadata"])
        metadata.pop("scene_token", None)
        metadata["source_file"] = source_entry
        portable_scene["metadata"] = metadata
        manifest, blocks = prepare_scene_binary(portable_scene, coordinate_dtype="float64")
        if len(manifest) > MAX_JSON_BYTES:
            raise ValueError("BITSAM scene metadata exceeds the size limit")
        write_entry("scene.bin", iter_scene_binary(manifest, blocks))
        if trace_mesh is not None:
            manifest, blocks = prepare_scene_binary(
                {"mesh": trace_mesh, "components": []}, coordinate_dtype="float64",
            )
            write_entry("trace.bin", iter_scene_binary(manifest, blocks))
        manifest_data = {
            "schema_version": PACKAGE_SCHEMA,
            "cache_contract": CACHE_CONTRACT,
            "scope": "active-case",
            "source": source_entry,
            "trace_cached": trace_mesh is not None,
            "entries": entries,
        }
        archive.writestr("manifest.json", b"".join(json_chunks(manifest_data)))
    if destination.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError("BITSAM package exceeds the size limit")
    return manifest_data


def extract_package(package: Path, destination: Path) -> dict[str, Any]:
    if package.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError("BITSAM package exceeds the size limit")
    with zipfile.ZipFile(package) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)) or len(names) > 8:
            raise ValueError("BITSAM contains duplicate or unexpected entries")
        if "manifest.json" not in names or archive.getinfo("manifest.json").file_size > CHUNK_BYTES:
            raise ValueError("BITSAM manifest is missing or oversized")
        manifest = json.loads(archive.read("manifest.json"))
        if not isinstance(manifest, dict) or manifest.get("schema_version") != PACKAGE_SCHEMA:
            raise ValueError("Unsupported BITSAM package version")
        if manifest.get("cache_contract") != CACHE_CONTRACT:
            raise ValueError("Unsupported BITSAM geometry cache. Open with a compatible app version.")
        source = manifest.get("source")
        allowed_sources = {"cad/original" + suffix for suffix in SOURCE_SUFFIXES}
        if source not in allowed_sources or type(manifest.get("trace_cached")) is not bool:
            raise ValueError("Invalid BITSAM source metadata")
        required = {"manifest.json", "project.json", "scene.bin", source}
        if manifest["trace_cached"]:
            required.add("trace.bin")
        entries = manifest.get("entries")
        if set(names) != required or not isinstance(entries, dict) or set(entries) != required - {"manifest.json"}:
            raise ValueError("BITSAM contains missing or unexpected entries")
        if sum(info.file_size for info in infos) > MAX_PACKAGE_BYTES:
            raise ValueError("Expanded BITSAM exceeds the size limit")
        for name, expected in entries.items():
            info = archive.getinfo(name)
            if not isinstance(expected, dict) or expected.get("size") != info.file_size:
                raise ValueError("BITSAM entry size mismatch: " + name)
            if info.flag_bits & 1 or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise ValueError("Unsupported BITSAM compression or encryption")
            if name == "project.json" and info.file_size > MAX_JSON_BYTES:
                raise ValueError("BITSAM project metadata exceeds the size limit")
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            count = 0
            with archive.open(info) as stream, target.open("wb") as output:
                while chunk := stream.read(CHUNK_BYTES):
                    count += len(chunk)
                    if count > info.file_size:
                        raise ValueError("BITSAM entry expanded beyond declared size")
                    output.write(chunk)
                    digest.update(chunk)
            if count != info.file_size or digest.hexdigest() != expected.get("sha256"):
                raise ValueError("BITSAM checksum mismatch: " + name)
        (destination / "manifest.json").write_bytes(b"".join(json_chunks(manifest)))
    return manifest


def read_scene_cache(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        header = stream.read(HEADER.size)
        if len(header) != HEADER.size:
            raise ValueError("Truncated BITSAM geometry header")
        magic, version, manifest_size = HEADER.unpack(header)
        if magic != MAGIC or version != VERSION or manifest_size > MAX_JSON_BYTES:
            raise ValueError("Invalid BITSAM geometry header")
        manifest = json.loads(stream.read(manifest_size))
        binary = manifest["binary"]
        data_offset = HEADER.size + manifest_size
        byte_length = binary["byte_length"]
        if binary.get("byte_order") != "little" or path.stat().st_size != data_offset + byte_length:
            raise ValueError("BITSAM geometry byte count mismatch")
        widths = {
            "vertices": ("float64", 3), "faces": ("uint32", 3),
            "face_component_ids": ("int32", 1), "face_material_codes": ("uint32", 1),
            "face_source_ids": ("uint32", 1), "face_areas_mm2": ("float64", 1),
            "feature_edge_points": ("float64", 6),
            "feature_edge_component_ids": ("int32", 1), "component_face_indices": ("uint32", 1),
        }
        descriptors = binary["arrays"]
        if set(descriptors) - set(widths) or set(widths) - {"face_material_codes"} - set(descriptors):
            raise ValueError("Invalid BITSAM geometry arrays")
        arrays = {}
        for name, descriptor in descriptors.items():
            dtype, width = widths[name]
            if descriptor["dtype"] != dtype or descriptor["width"] != width:
                raise ValueError("BITSAM geometry precision/layout mismatch")
            count = descriptor["count"]
            offset = descriptor["byte_offset"]
            size = descriptor["byte_length"]
            typecode = {"float64": "d", "int32": "i", "uint32": "I"}[dtype]
            values = array(typecode)
            if any(type(value) is not int or value < 0 for value in (count, offset, size)):
                raise ValueError("Invalid BITSAM geometry dimensions")
            if size != count * width * values.itemsize or offset + size > byte_length:
                raise ValueError("BITSAM geometry array out of bounds")
            stream.seek(data_offset + offset)
            remaining = size
            while remaining:
                chunk = stream.read(min(CHUNK_BYTES, remaining))
                if not chunk:
                    raise ValueError("Truncated BITSAM geometry array")
                values.frombytes(chunk)
                remaining -= len(chunk)
            if sys.byteorder != "little":
                values.byteswap()
            if dtype == "float64" and not all(math.isfinite(value) for value in values):
                raise ValueError("Non-finite BITSAM geometry coordinate")
            arrays[name] = values

    vertex_count = len(arrays["vertices"]) // 3
    face_count = len(arrays["faces"]) // 3
    if arrays["faces"] and max(arrays["faces"]) >= vertex_count:
        raise ValueError("BITSAM face vertex out of range")
    for name in ("face_component_ids", "face_source_ids", "face_areas_mm2"):
        if len(arrays[name]) not in {0, face_count}:
            raise ValueError("BITSAM per-face array count mismatch")
    table = binary["face_material_table"]
    if not isinstance(table, list) or not all(isinstance(value, str) for value in table):
        raise ValueError("Invalid BITSAM material table")
    codes = arrays.get("face_material_codes")
    if codes is not None:
        if len(codes) != face_count or (codes and max(codes) >= len(table)):
            raise ValueError("Invalid BITSAM material references")
        materials = [table[code] for code in codes]
    else:
        materials = list(repeat(table[0] if table else "default", face_count))
    edges = NumericRows(arrays["feature_edge_points"], 6)
    edge_components = arrays["feature_edge_component_ids"]
    if len(edges) != len(edge_components):
        raise ValueError("BITSAM edge metadata count mismatch")
    components = manifest["components"]
    if not isinstance(components, list):
        raise ValueError("Invalid BITSAM component metadata")
    metadata = manifest.get("metadata") or {}
    for key, actual in (("face_count", face_count), ("vertex_count", vertex_count), ("component_count", len(components))):
        if key in metadata and metadata[key] != actual:
            raise ValueError("BITSAM geometry metadata count mismatch")
    for component in components:
        count = component.pop("binary_face_count")
        encoding = component.pop("binary_face_encoding")
        if type(count) is not int or count < 0 or count > face_count:
            raise ValueError("Invalid BITSAM component face count")
        if encoding == "range":
            start = component.pop("binary_face_start")
            if type(start) is not int or start < 0 or start + count > face_count:
                raise ValueError("BITSAM component face range out of bounds")
            component["face_indices"] = list(range(start, start + count))
        elif encoding == "array":
            offset = component.pop("binary_face_offset")
            indices = arrays["component_face_indices"]
            if type(offset) is not int or offset < 0 or offset + count > len(indices):
                raise ValueError("BITSAM component face array out of bounds")
            component["face_indices"] = list(indices[offset:offset + count])
        else:
            raise ValueError("Invalid BITSAM component face encoding")
        if any(index >= face_count for index in component["face_indices"]):
            raise ValueError("BITSAM component face index out of bounds")
    return {
        "schema_version": "mesh-scene.v1",
        "units": manifest.get("units"),
        "coordinate_system": manifest.get("coordinate_system"),
        "components": components,
        "objects": components,
        "metadata": manifest.get("metadata") or {},
        "mesh": {
            "vertices": NumericRows(arrays["vertices"], 3),
            "faces": NumericRows(arrays["faces"], 3),
            "face_component_ids": [None if value < 0 else value for value in arrays["face_component_ids"]],
            "face_material_ids": materials,
            "face_source_ids": arrays["face_source_ids"],
            "face_areas_mm2": arrays["face_areas_mm2"],
            "face_ids": [], "face_normals": [], "face_centroids": [],
            "feature_edge_segments": [
                {"start": edge[:3], "end": edge[3:], "component_id": None if component < 0 else component}
                for edge, component in zip(edges, edge_components)
            ],
        },
    }
