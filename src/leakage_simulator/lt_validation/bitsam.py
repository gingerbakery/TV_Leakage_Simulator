"""Read saved photometric results without restoring CAD or simulator state."""
from __future__ import annotations

import hashlib
import json
import math
import re
import zipfile
from pathlib import Path
from typing import Any


MAX_PACKAGE_BYTES = 8 * 1024**3
MAX_JSON_BYTES = 128 * 1024**2
MAX_MANIFEST_BYTES = 1024**2
MAX_CELLS = 1_000_000
PACKAGE_SCHEMA = "bitsam-project.v2"
SETTINGS_SCHEMAS = {"bitsam-project.v1", "bitsam-project.v1-bsdf1"}
CACHE_CONTRACT = "bitsam-lossless-scene.v1"


def _issue(code: str, message: str, severity: str = "error") -> dict:
    return {"code": code, "severity": severity, "message": message}


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _number(value: Any, *, positive: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number) or (number <= 0 if positive else number < 0):
        return None
    return number


def _vector(value: Any, *, unit: bool = False) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 3:
        return None
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in value):
        return None
    try:
        vector = [float(x) for x in value]
    except (ValueError, OverflowError):
        return None
    if not all(math.isfinite(x) for x in vector):
        return None
    if unit:
        length = math.hypot(*vector)
        if not math.isfinite(length) or length <= 1e-12:
            return None
        return [x / length for x in vector]
    return vector


def _resolution(value: Any) -> tuple[int, int] | None:
    if (not isinstance(value, list) or len(value) != 2
            or any(type(x) is not int or x <= 0 for x in value)):
        return None
    if value[0] * value[1] > MAX_CELLS:
        return None
    return value[0], value[1]


def _json_object(data: bytes) -> dict:
    def reject_constant(value: str) -> None:
        raise ValueError("BITSAM JSON contains a non-finite number: " + value)

    def unique_keys(pairs: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("BITSAM JSON contains duplicate keys")
            result[key] = value
        return result

    try:
        result = json.loads(data.decode("utf-8-sig"), parse_constant=reject_constant,
                            object_pairs_hook=unique_keys)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("BITSAM JSON is unreadable") from exc
    if not isinstance(result, dict):
        raise ValueError("BITSAM metadata must be an object")
    return result


def _read_entry(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > limit or info.flag_bits & 1:
        raise ValueError("BITSAM metadata is oversized or encrypted")
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise ValueError("Unsupported BITSAM compression")
    with archive.open(info) as stream:
        data = stream.read(limit + 1)
    if len(data) != info.file_size or len(data) > limit:
        raise ValueError("BITSAM metadata size mismatch")
    return data


def _read_project(path: Path) -> tuple[dict, dict]:
    if not path.is_file() or path.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError("BITSAM file is unavailable or exceeds the size limit")
    with path.open("rb") as stream:
        signature = stream.read(4)
    if signature != b"PK\x03\x04":
        if path.stat().st_size > MAX_JSON_BYTES:
            raise ValueError("BITSAM JSON metadata exceeds the size limit")
        return _json_object(path.read_bytes()), {"container": "json", "integrity_scope": "legacy_json"}
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or len(names) > 8:
                raise ValueError("BITSAM contains duplicate or unexpected entries")
            if "manifest.json" not in names:
                raise ValueError("BITSAM manifest is missing")
            manifest = _json_object(_read_entry(archive, "manifest.json", MAX_MANIFEST_BYTES))
            if manifest.get("schema_version") != PACKAGE_SCHEMA:
                raise ValueError("Unsupported BITSAM package version")
            if manifest.get("cache_contract") != CACHE_CONTRACT:
                raise ValueError("Unsupported BITSAM package metadata contract")
            source = manifest.get("source")
            if source not in {"cad/original" + suffix for suffix in (".stp", ".step", ".obj", ".stl")}:
                raise ValueError("Invalid BITSAM source metadata")
            if type(manifest.get("trace_cached")) is not bool:
                raise ValueError("Invalid BITSAM trace metadata")
            required = {"manifest.json", "project.json", "scene.bin", source}
            if manifest["trace_cached"]:
                required.add("trace.bin")
            entries = manifest.get("entries")
            if (set(names) != required or not isinstance(entries, dict)
                    or set(entries) != required - {"manifest.json"}):
                raise ValueError("BITSAM contains missing or unexpected entries")
            if sum(info.file_size for info in infos) > MAX_PACKAGE_BYTES:
                raise ValueError("Expanded BITSAM exceeds the size limit")
            for name, expected in entries.items():
                info = archive.getinfo(name)
                if (not isinstance(expected, dict) or type(expected.get("size")) is not int
                        or expected["size"] != info.file_size
                        or not isinstance(expected.get("sha256"), str)
                        or not re.fullmatch(r"[0-9a-f]{64}", expected["sha256"])):
                    raise ValueError("BITSAM entry metadata mismatch: " + name)
                if info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise ValueError("Unsupported BITSAM compression or encryption")
            data = _read_entry(archive, "project.json", MAX_JSON_BYTES)
            if hashlib.sha256(data).hexdigest() != entries["project.json"]["sha256"]:
                raise ValueError("BITSAM checksum mismatch: project.json")
            return _json_object(data), {
                "container": "zip", "package_schema": PACKAGE_SCHEMA,
                "integrity_scope": "project_json_only",
                "project_sha256": entries["project.json"]["sha256"],
            }
    except (zipfile.BadZipFile, KeyError, RuntimeError, NotImplementedError, EOFError) as exc:
        raise ValueError("BITSAM ZIP metadata is unreadable") from exc


def _cross(left: list[float], right: list[float]) -> list[float]:
    return [left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0]]


def _edges(span: float | None, count: int | None) -> list[float] | None:
    if span is None or count is None:
        return None
    return [span * (index / count - 0.5) for index in range(count + 1)]


def _receiver(snapshot: dict, grid: dict | None, metrics: dict) -> dict:
    receiver_id = str(snapshot.get("receiver_id") or "unknown")
    issues: list[dict] = []
    resolution = _resolution(snapshot.get("resolution"))
    width, height = (_number(snapshot.get(key), positive=True) for key in ("width_mm", "height_mm"))
    normal = _vector(snapshot.get("normal"), unit=True)
    if type(snapshot.get("normal_flip")) is not bool:
        normal = None
    elif normal and snapshot["normal_flip"]:
        normal = [-value for value in normal]
    u_axis, v_axis = (_vector(snapshot.get(key), unit=True) for key in ("u_axis", "v_axis"))
    if u_axis and v_axis and abs(math.fsum(a * b for a, b in zip(u_axis, v_axis))) > 1e-6:
        issues.append(_issue("invalid_receiver_frame", "저장된 Receiver U/V 축이 직교하지 않습니다."))
    if normal and any(axis and abs(math.fsum(a * b for a, b in zip(normal, axis))) > 1e-6
                      for axis in (u_axis, v_axis)):
        issues.append(_issue("invalid_receiver_frame", "저장된 Receiver 법선과 U/V 축이 직교하지 않습니다."))
    if snapshot.get("enabled") is False and grid is not None:
        issues.append(_issue("disabled_receiver_grid", "비활성 Receiver 설정에 연결된 수광 격자가 있습니다."))
    metric = metrics.get(receiver_id)
    metric = metric if isinstance(metric, dict) else {}
    stored_total = _number(metric.get("total_flux_lumen"))
    if "total_flux_lumen" in metric and stored_total is None:
        issues.append(_issue("invalid_stored_flux", "저장된 총 수광량은 유한한 0 이상 lm 값이어야 합니다."))
    flux = None
    total = stored_total
    area = None
    if grid is None:
        issues.append(_issue("missing_flux_grid", "저장된 수광 flux 격자가 없습니다."))
    else:
        grid_resolution = _resolution(grid.get("resolution"))
        area = _number(grid.get("bin_area_mm2"), positive=True)
        if resolution is None or grid_resolution != resolution:
            issues.append(_issue("grid_snapshot_mismatch", "수광 격자 수와 실행 당시 Receiver 설정이 일치하지 않습니다."))
        candidate = grid.get("flux_lumen")
        valid = bool(grid_resolution and isinstance(candidate, list) and len(candidate) == grid_resolution[1])
        if valid:
            valid = all(isinstance(row, list) and len(row) == grid_resolution[0]
                        and all(_number(value) is not None for value in row) for row in candidate)
        if not valid:
            issues.append(_issue("invalid_flux_grid", "셀 flux는 격자 크기에 맞는 유한한 0 이상 lm 값이어야 합니다. Ray 수는 flux로 사용할 수 없습니다."))
        else:
            flux = [[float(value) for value in row] for row in candidate]
            try:
                total = math.fsum(value for row in flux for value in row)
            except OverflowError:
                total = None
            if total is None or not math.isfinite(total):
                flux = None
                total = stored_total
                issues.append(_issue("invalid_flux_total", "셀 flux 합계가 유한하지 않습니다."))
            elif stored_total is not None and not math.isclose(total, stored_total, rel_tol=1e-6, abs_tol=1e-12):
                issues.append(_issue("stored_flux_mismatch", "셀 flux 합계가 저장된 총 수광량과 일치하지 않습니다."))
        if area is None:
            issues.append(_issue("invalid_bin_area", "저장된 셀 면적이 없거나 올바르지 않습니다."))
        elif width and height and resolution:
            expected_area = width * height / (resolution[0] * resolution[1])
            if not math.isfinite(expected_area) or not math.isclose(area, expected_area, rel_tol=1e-6, abs_tol=1e-12):
                issues.append(_issue("bin_area_mismatch", "셀 면적과 Receiver 크기·격자 수가 일치하지 않습니다."))
    acceptance = _number(snapshot.get("acceptance_angle_deg"), positive=True)
    if acceptance is not None and acceptance > 180:
        acceptance = None
    return {
        "id": receiver_id, "name": str(snapshot.get("display_name") or receiver_id),
        "quantity": "photometric", "raw": True, "flux_lm": flux,
        "u_edges_mm": _edges(width, resolution[0] if resolution else None),
        "v_edges_mm": _edges(height, resolution[1] if resolution else None),
        "center_mm": _vector(snapshot.get("center")), "u_axis": u_axis, "v_axis": v_axis,
        "normal": normal, "acceptance_angle_deg": acceptance, "aperture": "none",
        "total_flux_lm": total, "issues": issues,
        "provenance": {"snapshot": "analysis_result.receivers", "row_order": "v_ascending",
                       "column_order": "u_ascending", "bin_area_mm2": area,
                       "stored_total_flux_lm": stored_total,
                       "hit_count": _number(grid.get("hit_count")) if grid else None},
    }


def _source(snapshot: dict, issues: list[dict]) -> dict:
    source_id = str(snapshot.get("emitter_id") or "unknown")
    if snapshot.get("emitter_type") not in ("datum_plane", "reference_plane"):
        issues.append(_issue("unsupported_source_geometry",
                             f"{source_id}: 실제 면 광원 형상은 저장된 사각 치수만으로 검증할 수 없습니다."))
    u_axis, v_axis = (_vector(snapshot.get(key), unit=True) for key in ("u_axis", "v_axis"))
    if u_axis and v_axis and abs(math.fsum(a * b for a, b in zip(u_axis, v_axis))) > 1e-6:
        issues.append(_issue("invalid_source_frame", f"{source_id}: 저장된 광원 U/V 축이 직교하지 않습니다."))
    normal = None
    if snapshot.get("emitter_type") in ("datum_plane", "reference_plane") and u_axis and v_axis:
        normal = _vector(_cross(u_axis, v_axis), unit=True)
    elif snapshot.get("normal_mode") == "custom":
        normal = _vector(snapshot.get("custom_normal"), unit=True)
    direction = snapshot.get("emission_direction")
    flip = snapshot.get("normal_flip")
    if direction in ("forward", "reverse"):
        flip = direction == "reverse"
    if direction == "both" or type(flip) is not bool:
        normal = None
    elif normal and flip:
        normal = [-value for value in normal]
    width, height = (_number(snapshot.get(key), positive=True) for key in ("width_mm", "height_mm"))
    mode = snapshot.get("power_mode")
    total = _number(snapshot.get("power_lumen"), positive=True) if mode == "total" else None
    power_field = {"total": "power_lumen", "power_per_area": "power_density_lm_per_m2",
                   "set_luminance": "luminance_nit"}.get(mode)
    if power_field in snapshot and _number(snapshot[power_field], positive=True) is None:
        issues.append(_issue("invalid_source_flux", f"{source_id}: 광원 출력 설정은 유한한 양수여야 합니다."))
    if mode in ("power_per_area", "set_luminance") and snapshot.get("emitter_type") in ("datum_plane", "reference_plane"):
        if width and height and snapshot.get("surface_construction") == "rectangular_fit":
            density = _number(snapshot.get("power_density_lm_per_m2" if mode == "power_per_area" else "luminance_nit"), positive=True)
            if density is not None:
                total = _number(density * width * height * 1e-6 * (math.pi if mode == "set_luminance" else 1), positive=True)
    aim_snapshot = snapshot.get("aim")
    aim = None
    reference = "emitted"
    distribution = snapshot.get("direction_distribution")
    if distribution not in ("lambertian", "isotropic", "gaussian"):
        distribution = None
    if aim_snapshot is not None and (not isinstance(aim_snapshot, dict)
                                     or type(aim_snapshot.get("enabled")) is not bool):
        reference = None
    if isinstance(aim_snapshot, dict) and aim_snapshot.get("enabled") is True:
        reference = "aim_region" if aim_snapshot.get("power_reference") == "aim_region" else None
        distribution = aim_snapshot.get("distribution")
        if distribution not in ("uniform_target_area", "uniform_solid_angle", "lambertian", "gaussian"):
            distribution = None
        aim = {
            "center_mm": _vector(aim_snapshot.get("center")),
            "u_axis": _vector(aim_snapshot.get("u_axis"), unit=True),
            "v_axis": _vector(aim_snapshot.get("v_axis"), unit=True),
            "width_mm": _number(aim_snapshot.get("width_mm"), positive=True),
            "height_mm": _number(aim_snapshot.get("height_mm"), positive=True),
            "distribution": distribution if isinstance(distribution, str) else None,
        }
        if aim_snapshot.get("mode", "area") != "area" or aim_snapshot.get("shape") != "rectangle":
            issues.append(_issue("unsupported_aim_geometry", f"{source_id}: 현재 비교 형식은 사각 Aim Area만 지원합니다."))
    if distribution == "gaussian":
        issues.append(_issue("unsupported_gaussian_width", f"{source_id}: Gaussian 각도 폭은 현재 비교 형식으로 검증할 수 없습니다."))
    if snapshot.get("surface_construction") == "polygon_auto":
        issues.append(_issue("unsupported_source_geometry", f"{source_id}: 다각형 광원 형상은 현재 비교 형식으로 검증할 수 없습니다."))
    return {
        "id": source_id, "name": source_id, "total_flux_lm": total, "flux_reference": reference,
        "center_mm": _vector(snapshot.get("center")), "u_axis": u_axis, "v_axis": v_axis,
        "width_mm": width, "height_mm": height, "normal": normal,
        "angular_distribution": distribution if isinstance(distribution, str) else None, "aim": aim,
    }


def read_bitsam(path: Path, filename: str | None = None) -> dict:
    """Return only saved result snapshots; never substitute editable workspace settings."""
    path = Path(path)
    try:
        project, provenance = _read_project(path)
    except OSError as exc:
        raise ValueError("BITSAM file is unreadable") from exc
    if project.get("format") != "tv-leakage-simulator-project" or project.get("schema_version") not in SETTINGS_SCHEMAS:
        raise ValueError("Unsupported BITSAM project format or version")
    dataset = {
        "schema": "lt-validation.v1", "kind": "bitsam", "filename": Path(filename or path.name).name,
        "receivers": [], "sources": [], "issues": [],
        "provenance": {**provenance, "settings_schema": project["schema_version"],
                       "project_name": _text(project.get("project_name")),
                       "saved_at": _text(project.get("saved_at")),
                       "application_version": _text(project.get("application_version"))},
    }
    result = project.get("analysis_result")
    if not isinstance(result, dict):
        dataset["issues"].append(_issue("missing_analysis_result", "저장된 해석 결과가 없습니다. 해석 결과를 포함해 저장한 BITSAM 파일이 필요합니다."))
        return dataset
    dataset["provenance"]["run_id"] = _text(result.get("run_id"))
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    grids = result.get("receiver_grids")
    snapshots = result.get("receivers")
    if not isinstance(grids, list) or not isinstance(snapshots, list):
        dataset["issues"].append(_issue("missing_result_receivers", "실행 당시 Receiver 또는 flux 격자 목록이 없습니다."))
        return dataset
    grid_by_id = {}
    for grid in grids:
        if not isinstance(grid, dict) or not isinstance(grid.get("receiver_id"), str):
            dataset["issues"].append(_issue("invalid_grid_receiver", "수광 격자의 Receiver 식별자가 올바르지 않습니다."))
            continue
        identifier = grid["receiver_id"]
        if identifier in grid_by_id:
            dataset["issues"].append(_issue("duplicate_receiver_grid", "같은 Receiver의 격자가 중복되어 있습니다."))
        else:
            grid_by_id[identifier] = grid
    seen = set()
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("receiver_id"), str):
            dataset["issues"].append(_issue("invalid_receiver_snapshot", "저장된 Receiver 조건이 올바르지 않습니다."))
            continue
        identifier = snapshot["receiver_id"]
        if identifier in seen:
            dataset["issues"].append(_issue("duplicate_receiver_snapshot", "실행 당시 Receiver 식별자가 중복되어 있습니다."))
            continue
        seen.add(identifier)
        if snapshot.get("enabled") is False and identifier not in grid_by_id:
            continue
        dataset["receivers"].append(_receiver(snapshot, grid_by_id.get(identifier), metrics))
    if set(grid_by_id) - seen:
        dataset["issues"].append(_issue("unmatched_receiver_grid", "실행 당시 Receiver 조건과 연결할 수 없는 격자가 있습니다."))
    emitters = result.get("emitters")
    if isinstance(emitters, list):
        source_ids = set()
        for emitter in emitters:
            if not isinstance(emitter, dict) or type(emitter.get("enabled")) is not bool:
                dataset["issues"].append(_issue("invalid_source_snapshot", "저장된 광원 활성 상태를 확인할 수 없습니다."))
                continue
            if emitter["enabled"] is False:
                continue
            identifier = emitter.get("emitter_id")
            if not isinstance(identifier, str) or identifier in source_ids:
                dataset["issues"].append(_issue("invalid_source_snapshot", "저장된 광원 식별자가 없거나 중복되어 있습니다."))
                continue
            source_ids.add(identifier)
            dataset["sources"].append(_source(emitter, dataset["issues"]))
    summary = metrics.get("_termination_summary")
    summary = summary if isinstance(summary, dict) else {}
    measured_source_total = _number(summary.get("source_power_lumen"), positive=True)
    if "source_power_lumen" in summary and measured_source_total is None:
        dataset["issues"].append(_issue("invalid_source_total", "저장된 광원 총광속은 유한한 양수여야 합니다."))
    if measured_source_total is not None:
        dataset["provenance"]["saved_source_total_flux_lm"] = measured_source_total
        sources = dataset["sources"]
        if len(sources) == 1 and sources[0]["total_flux_lm"] is None:
            sources[0]["total_flux_lm"] = measured_source_total
        if sources and all(source["total_flux_lm"] is not None for source in sources):
            try:
                total = math.fsum(source["total_flux_lm"] for source in sources)
            except OverflowError:
                total = math.inf
            if not math.isclose(total, measured_source_total, rel_tol=1e-6, abs_tol=1e-12):
                dataset["issues"].append(_issue("source_flux_mismatch", "저장된 광원 총광속과 실행 당시 광원 설정의 합계가 일치하지 않습니다."))
    return dataset
