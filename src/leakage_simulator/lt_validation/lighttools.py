"""Read saved LightTools data as data, never as ORACAD commands.

The saved format is not a documented exchange format.  This importer intentionally
keeps unverified coordinate conventions unknown.  A scalar conservation check
does not establish the ordering or physical orientation of a saved mesh.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math
from pathlib import Path
import re
from typing import Any


MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_CELLS = 1_000_000
_STRING = r'"(?:\\.|[^"\\])*"'
_TOKEN = re.compile(
    rf"(?P<string>{_STRING})|(?P<comment>\#[^\r\n]*)|"
    r"(?P<inline>\$(?P<inline_class>\w+)\s+create\s*->\s*(?P<inline_id>\$\w+)\s*;?\s*\{)|"
    r"(?P<get>\bget\w+\s*->\s*(?P<get_id>\$\w+)\s*\{)|"
    r"(?P<declare>\$(?P<declare_class>\w+)\s+create\s*->\s*(?P<declare_id>\$\w+))|"
    r"(?P<reference>(?P<reference_id>\$\w+)\s*\{)|(?P<open>\{)|(?P<close>\})|(?P<badquote>\")"
)
_FIELD = re.compile(rf"{_STRING}|\#[^\r\n]*|(?P<name>\b(?:set|restore)\w+)\s*:\s*")
_NUMBER = re.compile(r"[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?\Z")
_RELEVANT_FIELDS = {
    "setName", "setResponsivity", "setFluxUnits", "setIlluminanceUnits",
    "setPosition", "setOrientation", "setDataBounds", "setDoNoiseReduction",
    "setBilateralKernelSizeN", "setRayInfoToUse", "setTotalPower", "setSumsMesh",
    "setIntegrationInterval", "setMesh", "setEnabled", "setUnits",
}


@dataclass
class _Object:
    identity: str
    kind: str | None
    start: int
    body_start: int
    end: int = 0
    parent: _Object | None = None
    children: list[_Object] = field(default_factory=list)


def _objects(text: str) -> list[_Object]:
    """Balance braces while excluding quoted strings and hash comments.

    Array braces have their own stack entries.  Bare object-reference blocks
    resolve only against an earlier create declaration, not against an ID suffix.
    """
    declarations: dict[str, str] = {}
    stack: list[_Object | None] = []
    result: list[_Object] = []
    for token in _TOKEN.finditer(text):
        group = token.lastgroup
        if token.group("badquote"):
            raise ValueError("Unterminated quoted string in LTS")
        if token.group("string") or token.group("comment"):
            continue
        if token.group("declare"):
            identity = token.group("declare_id")
            kind = token.group("declare_class")
            if identity in declarations and declarations[identity] != kind:
                raise ValueError("LTS object identity has conflicting classes")
            declarations[identity] = kind
            continue
        if token.group("close"):
            if not stack:
                raise ValueError("Unbalanced closing brace in LTS")
            current = stack.pop()
            if current is not None:
                current.end = token.start()
            continue
        identity: str | None = None
        kind: str | None = None
        if token.group("inline"):
            identity, kind = token.group("inline_id"), token.group("inline_class")
            if identity in declarations and declarations[identity] != kind:
                raise ValueError("LTS object identity has conflicting classes")
            declarations[identity] = kind
        elif token.group("get"):
            identity = token.group("get_id")
            kind = declarations.get(identity)
        elif token.group("reference"):
            identity = token.group("reference_id")
            kind = declarations.get(identity)
        if identity is None:
            if group == "open":
                stack.append(None)
                if len(stack) > 512:
                    raise ValueError("LTS nesting limit exceeded")
            continue
        parent = next((item for item in reversed(stack) if item is not None), None)
        current = _Object(identity, kind, token.start(), token.end(), parent=parent)
        if parent:
            parent.children.append(current)
        result.append(current)
        stack.append(current)
        if len(stack) > 512:
            raise ValueError("LTS nesting limit exceeded")
    if stack:
        raise ValueError("Unbalanced opening brace or truncated LTS")
    return result


def _fields(text: str, obj: _Object) -> dict[str, str]:
    """Only read a block's direct fields; child mesh settings cannot leak up."""
    chunks: list[str] = []
    cursor = obj.body_start
    for child in obj.children:
        chunks.append(text[cursor:child.start])
        chunks.append("\n")
        cursor = child.end + 1
    chunks.append(text[cursor:obj.end])
    direct = "".join(chunks)
    fields: dict[str, str] = {}
    for token in _FIELD.finditer(direct):
        name = token.group("name")
        if name not in _RELEVANT_FIELDS:
            continue
        if name in fields:
            raise ValueError(f"Duplicate LTS field {name} in {obj.identity}")
        value_start = token.end()
        quoted = re.compile(_STRING).match(direct, value_start)
        if quoted:
            value_end = quoted.end()
            if not re.match(r"\s*;", direct[value_end:]):
                raise ValueError(f"Unterminated LTS field {name}")
        else:
            value_end = direct.find(";", value_start)
            if value_end < 0:
                raise ValueError(f"Unterminated LTS field {name}")
        fields[name] = direct[value_start:value_end].strip()
    return fields


def _string(fields: dict[str, str], name: str) -> str | None:
    value = fields.get(name)
    if value is None:
        return None
    if not re.fullmatch(_STRING, value):
        raise ValueError(f"Expected quoted string for {name}")
    # These escapes are enough for labels; never decode or evaluate commands.
    return value[1:-1].replace(r'\"', '"').replace(r"\\", "\\")


def _number(fields: dict[str, str], name: str) -> float | None:
    value = fields.get(name)
    if value is None:
        return None
    if not _NUMBER.fullmatch(value):
        raise ValueError(f"Invalid numeric LTS field {name}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Non-finite LTS field {name}")
    return number


def _array(fields: dict[str, str], name: str) -> tuple[list[int] | None, list[float]] | None:
    value = fields.get(name)
    if value is None:
        return None
    match = re.fullmatch(r"(?:\[([\d,\s]+)\]\s*)?\{([^{}]*)\}", value, re.DOTALL)
    if not match:
        raise ValueError(f"Invalid LTS array {name}")
    dimensions = [int(n.strip()) for n in match[1].split(",")] if match[1] else None
    if dimensions and (any(n <= 0 for n in dimensions) or math.prod(dimensions) > MAX_CELLS):
        raise ValueError(f"Invalid or oversized LTS dimensions for {name}")
    tokens = match[2].split()
    if len(tokens) > MAX_CELLS or (dimensions and math.prod(dimensions) != len(tokens)):
        raise ValueError(f"LTS dimensions do not match value count for {name}")
    if any(not _NUMBER.fullmatch(token) for token in tokens):
        raise ValueError(f"Invalid numeric array value in {name}")
    numbers = [float(token) for token in tokens]
    if not all(math.isfinite(number) for number in numbers):
        raise ValueError(f"Non-finite array value in {name}")
    return dimensions, numbers


def _ancestor(obj: _Object, predicate: Any) -> _Object | None:
    current = obj.parent
    while current is not None:
        if predicate(current):
            return current
        current = current.parent
    return None


def _issue(code: str, message: str, severity: str = "warning") -> dict[str, str]:
    return {"code": code, "severity": severity, "message": message}


def _receiver(text: str, mesh: _Object, field_cache: dict[int, dict[str, str]]) -> dict[str, Any]:
    def fields(obj: _Object) -> dict[str, str]:
        if obj.start not in field_cache:
            field_cache[obj.start] = _fields(text, obj)
        return field_cache[obj.start]

    owner = _ancestor(mesh, lambda obj: bool(obj.kind and obj.kind.endswith("ReceiverObj")))
    simulation = _ancestor(mesh, lambda obj: obj.kind == "ForwardSimFunction")
    item: dict[str, Any] = {
        "id": f"{owner.identity if owner else 'unscoped'}:{mesh.identity}",
        "name": owner.identity if owner else mesh.identity,
        "quantity": "unknown", "raw": None, "flux_lm": None,
        "u_edges_mm": None, "v_edges_mm": None,
        "center_mm": None, "u_axis": None, "v_axis": None, "normal": None,
        "acceptance_angle_deg": None, "aperture": None, "total_flux_lm": None,
        "issues": [], "provenance": {"mesh_object": mesh.identity},
    }
    issues = item["issues"]
    provenance = item["provenance"]
    if owner is None or simulation is None or _ancestor(simulation, lambda obj: obj is owner) is None:
        issues.append(_issue("lts_unscoped_mesh", "Receiver와 Forward Simulation의 소속을 확인할 수 없습니다."))
        return item
    try:
        owner_fields, sim_fields, mesh_fields = fields(owner), fields(simulation), fields(mesh)
        item["name"] = _string(owner_fields, "setName") or owner.identity
        responsivity = _string(owner_fields, "setResponsivity")
        units = _string(mesh_fields, "setFluxUnits")
        incident = _string(sim_fields, "setRayInfoToUse") == "Incident"
        item["quantity"] = {"Photometric": "photometric", "Radiometric": "radiometric"}.get(responsivity, "unknown")
        provenance.update({
            "receiver_object": owner.identity,
            "receiver_class": owner.kind,
            "simulation_object": simulation.identity,
            "responsivity": responsivity,
            "flux_units_setting": units,
            "illuminance_units_setting": _string(owner_fields, "setIlluminanceUnits"),
            "receiver_position_unverified": _array(owner_fields, "setPosition"),
            "receiver_orientation_unverified": _array(owner_fields, "setOrientation"),
            "data_bounds_unverified": _array(mesh_fields, "setDataBounds"),
            "smoothing_setting": _string(mesh_fields, "setDoNoiseReduction"),
            "smoothing_width_bins": _number(mesh_fields, "setBilateralKernelSizeN"),
            "display_mesh_ignored": "setMesh" in mesh_fields,
            "ray_info_to_use": _string(sim_fields, "setRayInfoToUse"),
            "simulation_enabled_setting": _string(sim_fields, "setEnabled"),
        })
        issues.append(_issue("lts_pose_unverified", "저장된 위치·방향의 세계좌표 대응과 수광 조건은 아직 검증되지 않았습니다."))
        # A display-unit setting can remain Lumen for a radiometric receiver.
        photometric = responsivity == "Photometric" and units == "Lumen"
        if not photometric:
            issues.append(_issue("lts_photometry_unverified", "Photometric·Lumen 조건을 확인할 수 없어 lm로 변환하지 않습니다."))
        if not incident:
            issues.append(_issue("lts_incident_basis_unverified", "Incident 수광 결과임을 확인할 수 없어 총수광량으로 사용하지 않습니다."))
        total = _number(mesh_fields, "setTotalPower")
        if total is not None and total < 0:
            raise ValueError("Stored TotalPower must not be negative")
        provenance["stored_total_power"] = total
        raw = _array(mesh_fields, "setSumsMesh")
        if raw is None:
            issues.append(_issue("lts_raw_mesh_missing", "원시 setSumsMesh가 없습니다. 표시용 setMesh는 사용하지 않습니다."))
            if photometric and incident:
                item["total_flux_lm"] = total
            return item
        dimensions, values = raw
        if dimensions is None or len(dimensions) != 2:
            raise ValueError("setSumsMesh must declare two dimensions")
        if any(value < 0 for value in values):
            raise ValueError("Raw SumsMesh contains negative flux")
        item["raw"] = True
        interval = _number(sim_fields, "setIntegrationInterval")
        provenance.update({
            "raw_field": "setSumsMesh", "raw_dimensions": dimensions,
            "raw_flat_values": values, "integration_interval": interval,
            "raw_order_verified": False,
        })
        if interval is None:
            issues.append(_issue("lts_interval_missing", "IntegrationInterval이 없어 원시 누적값을 변환할 수 없습니다."))
            if photometric and incident:
                item["total_flux_lm"] = total
            return item
        if interval <= 0:
            raise ValueError("IntegrationInterval must be positive")
        converted = [value * interval for value in values]
        if not all(math.isfinite(value) for value in converted):
            raise ValueError("Converted raw flux contains non-finite values")
        calculated = math.fsum(converted)
        provenance["raw_sum_times_interval"] = calculated
        if total is not None:
            difference = calculated - total
            agrees = abs(difference) <= 1e-12 + 1e-6 * abs(total)
            provenance.update({"total_power_difference": difference, "total_power_verified": agrees})
            if not agrees:
                issues.append(_issue("lts_total_power_mismatch", "ΣSumsMesh × IntegrationInterval이 TotalPower와 일치하지 않습니다.", "error"))
                return item
        else:
            provenance["total_power_verified"] = False
            issues.append(_issue("lts_total_power_missing", "저장된 TotalPower가 없어 합계를 독립적으로 검증할 수 없습니다."))
        if photometric and incident:
            item["total_flux_lm"] = total
            provenance["raw_flux_lm_flat"] = converted
        issues.append(_issue("lts_mesh_order_unverified", "LTS 배열의 U/V 순서가 미검증 상태여서 좌표 격자 비교를 보류합니다."))
    except (ValueError, OverflowError) as error:
        item["total_flux_lm"] = None
        issues.append(_issue("lts_invalid_mesh", str(error), "error"))
    return item


def read_lts(path: Path, filename: str | None = None) -> dict[str, Any]:
    """Read raw saved forward illuminance results into the validation contract.

    All coordinates and the flat-array order remain explicitly unverified.  An
    unsupported field is never resolved by fitting the other tool's result.
    """
    path = Path(path)
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
    except OSError as error:
        raise ValueError(f"Cannot read LTS file: {error}") from error
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("LTS file exceeds the 128 MiB import limit")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Unsupported binary or non-UTF-8 LTS encoding") from error
    if "\x00" in text or not re.match(r"#ORACAD Database File - Version [\d.]+(?:\r?\n|$)", text):
        raise ValueError("Only textual ORACAD LTS saved databases are supported")
    objects = _objects(text)
    if not objects:
        raise ValueError("LTS does not contain any readable objects")
    version_match = re.search(r"^#LightTools\s+([^\r\n]+)", text, re.MULTILINE)
    version = version_match[1].strip() if version_match else None
    observed = version in {"8.2.0", "8.3.2", "2024.09"}
    dataset: dict[str, Any] = {
        "schema": "lt-validation.v1", "kind": "lighttools",
        "filename": filename or path.name, "receivers": [], "sources": [],
        "issues": [
            _issue("lts_saved_format_experimental", "LTS 저장 형식 직접 읽기는 검증 중입니다. 알려진 예제로 확인한 합계 계산을 사용합니다."),
            _issue("lts_sources_unverified", "광원의 광속 기준·좌표·Aim 조건은 수동 보완이 필요합니다."),
        ],
        "provenance": {"sha256": hashlib.sha256(data).hexdigest(), "saved_lighttools_version": version,
                       "saved_format_version": text.splitlines()[0], "observed_example_version": observed,
                       "coordinate_convention_verified": False},
    }
    if not observed:
        dataset["issues"].append(_issue("lts_version_unverified", f"LightTools {version or '알 수 없음'} 실제 파일의 읽기 검증이 완료되지 않았습니다. 2025.03 지원이 검증된 상태는 아닙니다."))
    cache: dict[int, dict[str, str]] = {}
    managers = [obj for obj in objects if obj.kind == "ORALensDatabaseManagerObj"]
    if len(managers) == 1:
        manager_fields = _fields(text, managers[0])
        cache[managers[0].start] = manager_fields
        dataset["provenance"]["coordinate_units_setting"] = _string(manager_fields, "setUnits")
    identities: set[str] = set()
    for mesh in objects:
        if mesh.kind != "ORAIlluminanceDataMeshObj":
            continue
        if mesh.identity in identities:
            raise ValueError("Repeated LTS mesh object blocks are ambiguous")
        identities.add(mesh.identity)
        # Intensity, luminance and backward meshes are never interpreted as
        # forward incident illuminance, even if their field names look similar.
        if _ancestor(mesh, lambda obj: obj.kind == "BackwardSimFunction") is not None:
            continue
        dataset["receivers"].append(_receiver(text, mesh, cache))
    if not dataset["receivers"]:
        dataset["issues"].append(_issue("lts_results_missing", "읽을 수 있는 Forward Illuminance 저장 결과가 없습니다."))
    return dataset
