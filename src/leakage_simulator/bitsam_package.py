from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import zipfile
from array import array
from collections.abc import Sequence
from itertools import repeat
from pathlib import Path
from typing import Any, Iterable

from .scene_binary import HEADER, MAGIC, VERSION, iter_scene_binary, prepare_scene_binary


PACKAGE_SCHEMA = "bitsam-project.v2"
WORKSPACE_SCHEMA = "bitsam-workspace.v1"
WORKSPACE_FORMAT = "tv-leakage-simulator-workspace"
SETTINGS_SCHEMAS = {
    "bitsam-project.v1",
    "bitsam-project.v1-bsdf1",
    "bitsam-project.v1-preview1",
}
CACHE_CONTRACT = "bitsam-lossless-scene.v1"
MEDIA_TYPE = "application/vnd.bitsam+zip"
MAX_PACKAGE_BYTES = 8 * 1024**3
MAX_WORKSPACE_BYTES = MAX_PACKAGE_BYTES
MAX_JSON_BYTES = 128 * 1024**2
MAX_WORKSPACE_CASES = 64
CHUNK_BYTES = 1024**2
SOURCE_SUFFIXES = {".stp", ".step", ".obj", ".stl"}
_WORKSPACE_CASE_ENTRY = re.compile(r"cases/[0-9]{4}\.bitsam")
_WORKSPACE_ASSET_ENTRY = re.compile(
    r"assets/[0-9]{4}/accessory-[0-9]{4}\.(?:stp|step|obj|stl)",
)


class CumulativeByteBudget:
    """Fail-closed shared byte budget for a multi-entry workspace operation."""

    def __init__(self, limit: int = MAX_WORKSPACE_BYTES) -> None:
        if type(limit) is not int or limit < 0:
            raise ValueError("Invalid BITSAM workspace size limit")
        self.limit = limit
        self.used = 0

    def reserve(self, amount: int) -> None:
        if type(amount) is not int or amount < 0 or amount > self.limit - self.used:
            raise ValueError("BITSAM workspace exceeds the cumulative size limit")
        self.used += amount


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


def package_schema(path: Path) -> str:
    """Read only the bounded manifest needed to select a package reader."""
    if path.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError("BITSAM package exceeds the size limit")
    with zipfile.ZipFile(path) as archive:
        try:
            info = archive.getinfo("manifest.json")
        except KeyError as exc:
            raise ValueError("BITSAM manifest is missing or oversized") from exc
        if info.file_size > CHUNK_BYTES:
            raise ValueError("BITSAM manifest is missing or oversized")
        manifest = json.loads(archive.read(info))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("schema_version"), str):
        raise ValueError("Unsupported BITSAM package version")
    return manifest["schema_version"]


def _write_zip_entry(
    archive: zipfile.ZipFile,
    name: str,
    chunks: Iterable[bytes],
    *,
    limit: int,
    byte_budget: CumulativeByteBudget | None = None,
) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    with archive.open(name, "w", force_zip64=True) as output:
        for chunk in chunks:
            if byte_budget is not None:
                byte_budget.reserve(len(chunk))
            output.write(chunk)
            digest.update(chunk)
            size += len(chunk)
            if size > limit:
                raise ValueError("BITSAM entry exceeds the size limit")
    return {"size": size, "sha256": digest.hexdigest()}


def mesh_identity(mesh: dict[str, Any]) -> str:
    payload = {"mesh": mesh, "components": []}
    manifest, blocks = prepare_scene_binary(payload, coordinate_dtype="float64")
    digest = hashlib.sha256()
    for chunk in iter_scene_binary(manifest, blocks):
        digest.update(chunk)
    return digest.hexdigest()


def _javascript_number(value: float | int) -> str:
    """Return JSON.stringify-compatible text for a finite JavaScript Number.

    Geometry fingerprints are authored by the browser.  Python's JSON encoder
    writes values such as ``1.0`` and ``1e-07`` differently from
    ``JSON.stringify`` (``1`` and ``1e-7``), so hashing ``json.dumps`` output
    would reject otherwise valid projects.
    """
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("BITSAM geometry fingerprint contains a non-finite value")
    if number == 0:
        return "0"

    raw = repr(number).lower()
    sign = ""
    if raw.startswith("-"):
        sign, raw = "-", raw[1:]
    if "e" in raw:
        mantissa, exponent_text = raw.split("e", 1)
        exponent = int(exponent_text)
    else:
        mantissa, exponent = raw, 0
    if "." in mantissa:
        whole, fraction = mantissa.split(".", 1)
    else:
        whole, fraction = mantissa, ""
    digits = whole + fraction
    decimal_position = len(whole) + exponent

    absolute = abs(number)
    if 1e-6 <= absolute < 1e21:
        if decimal_position <= 0:
            rendered = "0." + "0" * (-decimal_position) + digits
        elif decimal_position >= len(digits):
            rendered = digits + "0" * (decimal_position - len(digits))
        else:
            rendered = digits[:decimal_position] + "." + digits[decimal_position:]
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return sign + rendered

    digits = digits.rstrip("0") or "0"
    rendered = digits[0]
    if len(digits) > 1:
        rendered += "." + digits[1:]
    scientific_exponent = decimal_position - 1
    exponent_sign = "+" if scientific_exponent >= 0 else "-"
    return sign + rendered + "e" + exponent_sign + str(abs(scientific_exponent))


def _rounded_fingerprint_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("BITSAM geometry fingerprint contains an invalid bound")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("BITSAM geometry fingerprint contains a non-finite bound")
    # Match Math.round(value * 1_000_000) / 1_000_000.  Unlike Python's
    # round(), JavaScript resolves half values toward positive infinity.
    return math.floor(number * 1_000_000 + 0.5) / 1_000_000


def scene_geometry_signature(scene: dict[str, Any]) -> str:
    """Reproduce the browser's deterministic component geometry signature."""
    components = scene.get("components")
    if not isinstance(components, list):
        raise ValueError("BITSAM scene components are invalid")

    normalized: list[dict[str, Any]] = []
    component_ids: set[int] = set()
    for component in components:
        if not isinstance(component, dict):
            raise ValueError("BITSAM scene component is invalid")
        numeric: dict[str, int] = {}
        for key in ("component_id", "object_id", "face_count"):
            value = component.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("BITSAM scene component identity is invalid")
            numeric[key] = value
        if numeric["component_id"] in component_ids:
            raise ValueError("BITSAM scene component identity is duplicated")
        component_ids.add(numeric["component_id"])

        bounds: dict[str, list[float]] = {}
        for key in ("bbox_min", "bbox_max"):
            values = component.get(key)
            if not isinstance(values, (list, tuple)) or len(values) != 3:
                raise ValueError("BITSAM scene component bounds are invalid")
            bounds[key] = [_rounded_fingerprint_number(value) for value in values]
        normalized.append({**numeric, **bounds})
    normalized.sort(key=lambda item: item["component_id"])

    objects: list[str] = []
    for component in normalized:
        bbox_min = ",".join(_javascript_number(value) for value in component["bbox_min"])
        bbox_max = ",".join(_javascript_number(value) for value in component["bbox_max"])
        objects.append(
            '{"component_id":'
            + _javascript_number(component["component_id"])
            + ',"object_id":'
            + _javascript_number(component["object_id"])
            + ',"face_count":'
            + _javascript_number(component["face_count"])
            + ',"bbox_min":['
            + bbox_min
            + '],"bbox_max":['
            + bbox_max
            + "]}"
        )
    source = "[" + ",".join(objects) + "]"
    fingerprint = 0x811C9DC5
    for character in source:
        # The canonical source is ASCII, so code points and UTF-16 code units
        # are identical to JavaScript's charCodeAt values.
        fingerprint ^= ord(character)
        fingerprint = (fingerprint * 0x01000193) & 0xFFFFFFFF
    return "fnv1a32:{:08x}".format(fingerprint)


def write_package(
    destination: Path,
    project: dict[str, Any],
    source: Path,
    scene: dict[str, Any],
    trace_mesh: dict[str, Any] | None,
) -> dict[str, Any]:
    if project.get("schema_version") not in SETTINGS_SCHEMAS:
        raise ValueError("Unsupported BITSAM settings version")
    if not isinstance(project.get("workspace"), dict):
        raise ValueError("BITSAM workspace is missing")
    workspace = project["workspace"]
    has_preview = isinstance(project.get("leak_preview"), dict)
    if (project["schema_version"] == "bitsam-project.v1-preview1") != has_preview:
        raise ValueError("Leak Preview settings require bitsam-project.v1-preview1")
    has_bsdf = bool(workspace.get("bsdfAssets")) or any(
        isinstance(item, dict) and bool(item.get("bsdfAssetId"))
        for key in ("materialAssignments", "customOpticalProfiles")
        for item in (workspace.get(key) or [])
    )
    if has_bsdf and project["schema_version"] not in {
        "bitsam-project.v1-bsdf1",
        "bitsam-project.v1-preview1",
    }:
        raise ValueError(
            "BSDF assets/references require a BSDF-aware settings schema "
            "to prevent older builds from ignoring them"
        )
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


def _inspect_package_archive(
    archive: zipfile.ZipFile,
) -> tuple[dict[str, Any], list[tuple[str, zipfile.ZipInfo, dict[str, Any]]], int]:
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
    expanded_size = sum(info.file_size for info in infos)
    if expanded_size > MAX_PACKAGE_BYTES:
        raise ValueError("Expanded BITSAM exceeds the size limit")
    validated: list[tuple[str, zipfile.ZipInfo, dict[str, Any]]] = []
    for name, expected in entries.items():
        info = archive.getinfo(name)
        if not isinstance(expected, dict) or expected.get("size") != info.file_size:
            raise ValueError("BITSAM entry size mismatch: " + name)
        if info.flag_bits & 1 or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ValueError("Unsupported BITSAM compression or encryption")
        if name == "project.json" and info.file_size > MAX_JSON_BYTES:
            raise ValueError("BITSAM project metadata exceeds the size limit")
        validated.append((name, info, expected))
    return manifest, validated, expanded_size


def package_expanded_size(package: Path) -> int:
    """Validate a V2 package and report its declared expanded byte count."""
    if package.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError("BITSAM package exceeds the size limit")
    with zipfile.ZipFile(package) as archive:
        _, _, expanded_size = _inspect_package_archive(archive)
    return expanded_size


def extract_package(
    package: Path,
    destination: Path,
    *,
    byte_budget: CumulativeByteBudget | None = None,
) -> dict[str, Any]:
    if package.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError("BITSAM package exceeds the size limit")
    with zipfile.ZipFile(package) as archive:
        manifest, validated, expanded_size = _inspect_package_archive(archive)
        if byte_budget is not None:
            # Reserve the complete central-directory declaration before
            # creating any output for this nested package.
            byte_budget.reserve(expanded_size)
        for name, info, expected in validated:
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


def write_workspace_package(
    destination: Path,
    workspace: dict[str, Any],
    case_files: dict[str, Path],
    asset_files: dict[str, Path] | None = None,
) -> dict[str, Any]:
    """Write a workspace envelope containing independently portable V2 cases."""
    cases = workspace.get("cases")
    if (
        workspace.get("format") != WORKSPACE_FORMAT
        or workspace.get("schema_version") != WORKSPACE_SCHEMA
        or not isinstance(cases, list)
        or not 1 <= len(cases) <= MAX_WORKSPACE_CASES
    ):
        raise ValueError("Invalid BITSAM workspace metadata")
    case_ids: list[str] = []
    entries: list[str] = []
    accessory_entries: list[str] = []
    orders: list[int] = []
    for item in cases:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("case_id"), str)
            or not item["case_id"]
            or type(item.get("order")) is not int
            or item["order"] < 0
            or not isinstance(item.get("name"), str)
            or not isinstance(item.get("note"), str)
            or type(item.get("visible")) is not bool
            or not isinstance(item.get("entry"), str)
            or not _WORKSPACE_CASE_ENTRY.fullmatch(item["entry"])
            or not isinstance(item.get("source"), dict)
            or not isinstance(item["source"].get("display_name"), str)
            or not item["source"]["display_name"]
            or not isinstance(item.get("accessories"), list)
            or len(item["accessories"]) > 64
        ):
            raise ValueError("Invalid BITSAM workspace case metadata")
        accessory_ids: set[str] = set()
        for accessory in item["accessories"]:
            if (
                not isinstance(accessory, dict)
                or not isinstance(accessory.get("accessory_id"), str)
                or not accessory["accessory_id"]
                or accessory["accessory_id"] in accessory_ids
                or not isinstance(accessory.get("display_name"), str)
                or not accessory["display_name"]
                or type(accessory.get("visible")) is not bool
                or not isinstance(accessory.get("entry"), str)
                or not _WORKSPACE_ASSET_ENTRY.fullmatch(accessory["entry"])
            ):
                raise ValueError("Invalid BITSAM workspace Accessory CAD metadata")
            accessory_ids.add(accessory["accessory_id"])
            accessory_entries.append(accessory["entry"])
        case_ids.append(item["case_id"])
        entries.append(item["entry"])
        orders.append(item["order"])
    asset_files = asset_files or {}
    if (
        len(set(case_ids)) != len(case_ids)
        or len(set(orders)) != len(orders)
        or len(set(entries)) != len(entries)
        or set(entries) != set(case_files)
        or len(set(accessory_entries)) != len(accessory_entries)
        or set(accessory_entries) != set(asset_files)
        or workspace.get("active_case_id") not in set(case_ids)
    ):
        raise ValueError("Invalid BITSAM workspace case metadata")
    if any(not path.is_file() for path in [*case_files.values(), *asset_files.values()]):
        raise ValueError("BITSAM workspace case package is unavailable")

    # Reject the known lower bound before opening the destination. This avoids
    # streaming many individually valid Case packages into an oversized outer
    # Workspace and then discovering the aggregate limit only at the end.
    workspace_json_size = sum(len(chunk) for chunk in json_chunks(workspace))
    known_entry_size = workspace_json_size + sum(
        path.stat().st_size for path in [*case_files.values(), *asset_files.values()]
    )
    if known_entry_size > MAX_WORKSPACE_BYTES:
        raise ValueError("BITSAM workspace exceeds the cumulative size limit")

    entry_metadata: dict[str, Any] = {}
    byte_budget = CumulativeByteBudget(MAX_WORKSPACE_BYTES)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        entry_metadata["workspace.json"] = _write_zip_entry(
            archive,
            "workspace.json",
            json_chunks(workspace),
            limit=MAX_JSON_BYTES,
            byte_budget=byte_budget,
        )
        for entry in entries:
            entry_metadata[entry] = _write_zip_entry(
                archive,
                entry,
                file_chunks(case_files[entry]),
                limit=MAX_PACKAGE_BYTES,
                byte_budget=byte_budget,
            )
        for entry, source in asset_files.items():
            entry_metadata[entry] = _write_zip_entry(
                archive,
                entry,
                file_chunks(source),
                limit=MAX_PACKAGE_BYTES,
                byte_budget=byte_budget,
            )
        manifest = {
            "schema_version": WORKSPACE_SCHEMA,
            "scope": "workspace",
            "workspace": "workspace.json",
            "case_count": len(cases),
            "active_case_id": workspace["active_case_id"],
            "entries": entry_metadata,
        }
        manifest_bytes = b"".join(json_chunks(manifest))
        byte_budget.reserve(len(manifest_bytes))
        archive.writestr("manifest.json", manifest_bytes)
    if destination.stat().st_size > MAX_WORKSPACE_BYTES:
        raise ValueError("BITSAM package exceeds the size limit")
    return manifest


def extract_workspace_package(package: Path, destination: Path) -> dict[str, Any]:
    """Validate and extract the outer multi-case workspace container."""
    if package.stat().st_size > MAX_WORKSPACE_BYTES:
        raise ValueError("BITSAM package exceeds the size limit")
    with zipfile.ZipFile(package) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)) or len(names) > MAX_WORKSPACE_CASES * 65 + 2:
            raise ValueError("BITSAM workspace contains duplicate or unexpected entries")
        if "manifest.json" not in names or archive.getinfo("manifest.json").file_size > CHUNK_BYTES:
            raise ValueError("BITSAM manifest is missing or oversized")
        manifest = json.loads(archive.read("manifest.json"))
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != WORKSPACE_SCHEMA
            or manifest.get("scope") != "workspace"
            or manifest.get("workspace") != "workspace.json"
        ):
            raise ValueError("Unsupported BITSAM workspace version")
        entries = manifest.get("entries")
        if not isinstance(entries, dict) or set(entries) != set(names) - {"manifest.json"}:
            raise ValueError("BITSAM workspace contains missing or unexpected entries")
        case_names = sorted(name for name in entries if _WORKSPACE_CASE_ENTRY.fullmatch(name))
        asset_names = sorted(name for name in entries if _WORKSPACE_ASSET_ENTRY.fullmatch(name))
        if (
            "workspace.json" not in entries
            or not 1 <= len(case_names) <= MAX_WORKSPACE_CASES
            or set(entries) != {"workspace.json", *case_names, *asset_names}
            or manifest.get("case_count") != len(case_names)
        ):
            raise ValueError("BITSAM workspace case entries are invalid")
        if sum(info.file_size for info in infos) > MAX_WORKSPACE_BYTES:
            raise ValueError("Expanded BITSAM workspace exceeds the size limit")
        for name, expected in entries.items():
            info = archive.getinfo(name)
            if (
                not isinstance(expected, dict)
                or expected.get("size") != info.file_size
                or not isinstance(expected.get("sha256"), str)
            ):
                raise ValueError("BITSAM workspace entry size mismatch: " + name)
            if info.flag_bits & 1 or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise ValueError("Unsupported BITSAM workspace compression or encryption")
            if name == "workspace.json" and info.file_size > MAX_JSON_BYTES:
                raise ValueError("BITSAM workspace metadata exceeds the size limit")
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            count = 0
            with archive.open(info) as stream, target.open("wb") as output:
                while chunk := stream.read(CHUNK_BYTES):
                    count += len(chunk)
                    if count > info.file_size:
                        raise ValueError("BITSAM workspace entry expanded beyond declared size")
                    output.write(chunk)
                    digest.update(chunk)
            if count != info.file_size or digest.hexdigest() != expected["sha256"]:
                raise ValueError("BITSAM workspace checksum mismatch: " + name)
        (destination / "manifest.json").write_bytes(b"".join(json_chunks(manifest)))

    workspace = read_json(destination / "workspace.json")
    cases = workspace.get("cases")
    if (
        workspace.get("format") != WORKSPACE_FORMAT
        or workspace.get("schema_version") != WORKSPACE_SCHEMA
        or not isinstance(workspace.get("workspace_name"), str)
        or not workspace["workspace_name"]
        or len(workspace["workspace_name"]) > 256
        or not isinstance(cases, list)
        or not 1 <= len(cases) <= MAX_WORKSPACE_CASES
    ):
        raise ValueError("Invalid BITSAM workspace metadata")
    case_ids: list[str] = []
    case_entries: list[str] = []
    referenced_assets: list[str] = []
    orders: list[int] = []
    for item in cases:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("case_id"), str)
            or not item["case_id"]
            or type(item.get("order")) is not int
            or item["order"] < 0
            or not isinstance(item.get("name"), str)
            or not isinstance(item.get("note"), str)
            or type(item.get("visible")) is not bool
            or not isinstance(item.get("entry"), str)
            or not _WORKSPACE_CASE_ENTRY.fullmatch(item["entry"])
            or not isinstance(item.get("source"), dict)
            or not isinstance(item["source"].get("display_name"), str)
            or not item["source"]["display_name"]
            or not isinstance(item.get("accessories"), list)
            or len(item["accessories"]) > 64
        ):
            raise ValueError("Invalid BITSAM workspace case metadata")
        accessory_ids: set[str] = set()
        for accessory in item["accessories"]:
            if (
                not isinstance(accessory, dict)
                or not isinstance(accessory.get("accessory_id"), str)
                or not accessory["accessory_id"]
                or accessory["accessory_id"] in accessory_ids
                or not isinstance(accessory.get("display_name"), str)
                or not accessory["display_name"]
                or type(accessory.get("visible")) is not bool
                or not isinstance(accessory.get("entry"), str)
                or not _WORKSPACE_ASSET_ENTRY.fullmatch(accessory["entry"])
            ):
                raise ValueError("Invalid BITSAM workspace Accessory CAD metadata")
            accessory_ids.add(accessory["accessory_id"])
            referenced_assets.append(accessory["entry"])
        case_ids.append(item["case_id"])
        case_entries.append(item["entry"])
        orders.append(item["order"])
    if (
        len(set(case_ids)) != len(case_ids)
        or len(set(case_entries)) != len(case_entries)
        or len(set(orders)) != len(orders)
        or set(case_entries) != set(case_names)
        or len(set(referenced_assets)) != len(referenced_assets)
        or set(referenced_assets) != set(asset_names)
        or workspace.get("active_case_id") not in set(case_ids)
        or manifest.get("active_case_id") != workspace.get("active_case_id")
    ):
        raise ValueError("Invalid BITSAM workspace case identity")
    return {"manifest": manifest, "workspace": workspace}


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
