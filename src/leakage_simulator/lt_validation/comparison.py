"""Strict, coordinate-based comparisons of photometric receiver results.

This module never changes a grid's sampling, estimates a missing condition, or
searches for a transform that makes the numerical results look more similar.
"""
from __future__ import annotations

from bisect import bisect_right
from math import fsum, isclose, isfinite, sqrt
import re
from typing import Any

MAX_CELLS = 1_000_000
POSITION_TOL = 1e-6  # mm; accommodates saved float32 geometry, not setup changes
REL_TOL = 1e-6


def issue(code: str, message: str, severity: str = "error") -> dict:
    return {"code": code, "message": message, "severity": severity}


def number(value: Any) -> bool:
    try:
        return type(value) in (int, float) and isfinite(value)
    except OverflowError:
        return False


def close(a: float, b: float, *, position: bool = False) -> bool:
    return isclose(a, b, rel_tol=0 if position else REL_TOL,
                   abs_tol=POSITION_TOL if position else 1e-12)


def vector(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 3 and all(number(x) for x in value)


def dot(a: list, b: list) -> float:
    return fsum(x * y for x, y in zip(a, b))


def vector_close(a: list, b: list) -> bool:
    return all(close(x, y, position=True) for x, y in zip(a, b))


def edges_valid(edges: Any) -> bool:
    return (isinstance(edges, list) and 2 <= len(edges) <= MAX_CELLS + 1
            and all(number(x) for x in edges)
            and all(b > a for a, b in zip(edges, edges[1:])))


def valid_grid(receiver: dict) -> tuple[float | None, list[dict]]:
    """Return the physical flux sum only for a complete, finite, valid mesh."""
    errors: list[dict] = []
    flux = receiver.get("flux_lm")
    u, v = receiver.get("u_edges_mm"), receiver.get("v_edges_mm")
    if flux is None:
        return None, [issue("missing_raw_mesh", "원시 셀 수광량 또는 좌표 배열이 없습니다.")]
    if not edges_valid(u) or not edges_valid(v):
        return None, [issue("invalid_edges", "셀 경계 좌표가 없거나 증가 순서가 아닙니다.")]
    nx, ny = len(u) - 1, len(v) - 1
    if nx * ny > MAX_CELLS or not isinstance(flux, list) or len(flux) != ny:
        return None, [issue("invalid_shape", "격자 크기 또는 행 수가 올바르지 않습니다.")]
    if any(not isinstance(row, list) or len(row) != nx for row in flux):
        return None, [issue("invalid_shape", "격자 열 수가 좌표와 다릅니다.")]
    areas = [(u1-u0) * (v1-v0) * 1e-6 for u0, u1 in zip(u, u[1:]) for v0, v1 in zip(v, v[1:])]
    if any(not isfinite(area) or area <= 0 for area in areas):
        return None, [issue("invalid_area", "셀 면적을 유효한 m² 값으로 계산할 수 없습니다.")]
    if any(not number(x) or x < 0 for row in flux for x in row):
        return None, [issue("invalid_flux", "셀 수광량에 음수 또는 유효하지 않은 수치가 있습니다.")]
    try:
        total = fsum(x for row in flux for x in row)
    except OverflowError:
        return None, [issue("invalid_flux", "수광량 합계가 유효 범위를 초과합니다.")]
    if not isfinite(total):
        return None, [issue("invalid_flux", "수광량 합계가 유효하지 않습니다.")]
    errors += _extracted_mesh_errors(receiver)
    stored = receiver.get("total_flux_lm")
    if not number(stored) or stored < 0:
        errors.append(issue("missing_total", "저장된 총 수광량(lm)이 없거나 유효하지 않습니다."))
    else:
        rounding = receiver.get("total_flux_tolerance_lm", 1e-12)
        if not number(rounding) or not 0 <= rounding <= 5e-8:
            errors.append(issue("invalid_total_tolerance", "총광속 반올림 허용량은 0~0.00000005 lm이어야 합니다."))
        elif not isclose(total, stored, rel_tol=REL_TOL, abs_tol=rounding):
            errors.append(issue("total_mismatch", f"셀 합계 {total:.10g} lm와 저장 총광속 {stored:.10g} lm가 다릅니다."))
    return total, errors


def _extracted_mesh_errors(receiver: dict) -> list[dict]:
    """Keep a supplemented LTS mesh tied to its extracted, unmodified cells."""
    provenance = receiver.get("provenance")
    extracted_fields = ("raw_flux_lm_flat", "raw_dimensions", "raw_field",
                        "raw_flat_values", "integration_interval", "user_confirmed_extracted_order")
    if not isinstance(provenance, dict) or not any(key in provenance for key in extracted_fields):
        return []
    values = provenance.get("raw_flux_lm_flat")
    dimensions = provenance.get("raw_dimensions")
    flux = receiver["flux_lm"]
    ny, nx = len(flux), len(flux[0])
    if (not isinstance(dimensions, list) or len(dimensions) != 2
            or any(type(n) is not int or n <= 0 for n in dimensions)
            or dimensions not in ([nx, ny], [ny, nx])
            or not isinstance(values, list) or len(values) != nx * ny
            or any(not number(x) or x < 0 for x in values)):
        return [issue("extracted_mesh_mismatch", "입력 격자가 추출한 LT 원본의 치수·셀 수와 다릅니다.")]
    order = provenance.get("user_confirmed_extracted_order")
    match = re.fullmatch(r"fast=([UV]);U=(ascending|descending);V=(ascending|descending)", order or "") if isinstance(order, str) else None
    if match is None:
        return [issue("missing_extracted_order", "추출한 LT 배열의 빠른 축과 U/V 저장 순서를 확인해야 합니다.")]
    fast, u_order, v_order = match.groups()
    for v, row in enumerate(flux):
        for u, value in enumerate(row):
            raw_u = u if u_order == "ascending" else nx - 1 - u
            raw_v = v if v_order == "ascending" else ny - 1 - v
            expected = values[raw_v * nx + raw_u if fast == "U" else raw_u * ny + raw_v]
            # Only roundoff from an explicit lm/lx unit conversion is allowed.
            if not isclose(value, expected, rel_tol=1e-12, abs_tol=1e-18):
                return [issue("extracted_mesh_mismatch", "입력 셀값이 확인한 좌표 순서의 LT 원시값과 다릅니다.")]
    return []


def _basis_errors(value: dict, label: str) -> list[dict]:
    errors = []
    for field in ("center_mm", "u_axis", "v_axis", "normal"):
        if not vector(value.get(field)):
            errors.append(issue("missing_condition", f"{label}: {field} 좌표 확인이 필요합니다."))
    if errors:
        return errors
    u, v, n = (value[field] for field in ("u_axis", "v_axis", "normal"))
    if (any(abs(dot(a, a) - 1) > 2e-6 for a in (u, v, n))
            or any(abs(dot(a, b)) > 1e-6 for a, b in ((u, v), (u, n), (v, n)))):
        errors.append(issue("invalid_basis", f"{label}: U·V·법선은 서로 수직인 단위벡터여야 합니다."))
    return errors


def _receiver_errors(receiver: dict) -> list[dict]:
    errors = _basis_errors(receiver, "리시버")
    if receiver.get("quantity") != "photometric":
        errors.append(issue("not_photometric", "Photometric Illuminance/Flux 데이터가 아닙니다."))
    if receiver.get("raw") is not True:
        errors.append(issue("not_raw", "Unsmoothed 원시 셀 데이터 확인이 필요합니다."))
    angle = receiver.get("acceptance_angle_deg")
    if not number(angle) or not 0 < angle <= 90:
        errors.append(issue("missing_acceptance", "수광 허용 반각(0° 초과, 90° 이하) 확인이 필요합니다."))
    if receiver.get("aperture") != "none":
        errors.append(issue("unknown_aperture", "Aperture 없음 조건을 확인해야 합니다."))
    return errors


def _source_errors(sources: Any) -> list[dict]:
    if not isinstance(sources, list) or not sources:
        return [issue("missing_sources", "광원 설정 확인이 필요합니다.")]
    errors: list[dict] = []
    if len(sources) > 1000:
        return [issue("invalid_sources", "광원 수가 제한을 초과합니다.")]
    for index, source in enumerate(sources):
        label = f"광원 {index + 1}"
        if not isinstance(source, dict):
            errors.append(issue("invalid_source", f"{label}: 설정 형식이 올바르지 않습니다."))
            continue
        errors += _basis_errors(source, label)
        for key in ("total_flux_lm", "width_mm", "height_mm"):
            if not number(source.get(key)) or source[key] <= 0:
                errors.append(issue("missing_condition", f"{label}: {key} 확인이 필요합니다."))
        if source.get("flux_reference") not in ("aim_region", "emitted"):
            errors.append(issue("missing_flux_reference", f"{label}: 광속 기준 확인이 필요합니다."))
        if not source.get("angular_distribution"):
            errors.append(issue("missing_distribution", f"{label}: 방사 분포 확인이 필요합니다."))
        # These profiles need additional parameters not represented by this v1
        # contract. Accepting a name alone would silently equate different optics.
        if source.get("angular_distribution") not in ("lambertian", "isotropic", "uniform_target_area"):
            errors.append(issue("unsupported_distribution", f"{label}: 지원되지 않거나 추가 조건이 필요한 방사 분포입니다."))
        aim = source.get("aim")
        if source.get("flux_reference") == "aim_region":
            if not isinstance(aim, dict):
                errors.append(issue("missing_aim", f"{label}: Aim 영역 설정이 없습니다."))
            else:
                for key in ("center_mm", "u_axis", "v_axis"):
                    if not vector(aim.get(key)):
                        errors.append(issue("missing_aim", f"{label}: Aim {key} 확인이 필요합니다."))
                au, av = aim.get("u_axis"), aim.get("v_axis")
                if vector(au) and vector(av) and (abs(dot(au, au) - 1) > 2e-6 or abs(dot(av, av) - 1) > 2e-6 or abs(dot(au, av)) > 1e-6):
                    errors.append(issue("invalid_aim", f"{label}: Aim 축은 서로 수직인 단위벡터여야 합니다."))
                for key in ("width_mm", "height_mm"):
                    if not number(aim.get(key)) or aim[key] <= 0:
                        errors.append(issue("missing_aim", f"{label}: Aim {key} 확인이 필요합니다."))
                if aim.get("distribution") != "uniform_target_area":
                    errors.append(issue("unsupported_aim", f"{label}: 현재 비교는 uniform_target_area 사각 Aim 영역을 지원합니다."))
                if aim.get("distribution") != source.get("angular_distribution"):
                    errors.append(issue("ambiguous_distribution", f"{label}: 실제 방사 분포와 Aim 분포가 일치하지 않습니다."))
        elif aim is not None:
            errors.append(issue("ambiguous_aim", f"{label}: Aim 설정과 광속 기준이 일치하지 않습니다."))
        elif source.get("angular_distribution") == "uniform_target_area":
            errors.append(issue("missing_aim", f"{label}: Target 영역 분포에는 Aim 조건이 필요합니다."))
    return errors


def _equal_condition(a: Any, b: Any, path: str = "") -> bool:
    if number(a) and number(b):
        return close(a, b, position=any(s in path for s in ("center", "axis", "normal", "mm")))
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_equal_condition(x, y, path) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        # IDs and names are arbitrary labels, not physical conditions.
        keys = (set(a) | set(b)) - {"id", "name", "provenance"}
        return all(_equal_condition(a.get(k), b.get(k), k) for k in keys)
    return type(a) is type(b) and a == b


def _compare_sources(a: list, b: list) -> list[dict]:
    if len(a) != len(b):
        return [issue("source_mismatch", "광원 수가 다릅니다.")]
    # Match sources by their full physical settings, not arbitrary file IDs.
    remaining = list(b)
    for source in a:
        match = next((i for i, other in enumerate(remaining) if _equal_condition(source, other)), None)
        if match is None:
            return [issue("source_mismatch", "광원 위치·방향·크기·광속 기준·방사/Aim 조건이 다릅니다.")]
        remaining.pop(match)
    return []


def _align(reference: dict, other: dict) -> list[list[float]]:
    """Coordinate-proven bijection of equal grids; never interpolate or fit."""
    ru, rv = reference["u_edges_mm"], reference["v_edges_mm"]
    ou, ov = other["u_edges_mm"], other["v_edges_mm"]
    if len(ru) != len(ou) or len(rv) != len(ov):
        raise ValueError("격자 수가 달라 비교할 수 없습니다.")
    if not vector_close(reference["center_mm"], other["center_mm"]):
        raise ValueError("리시버 위치가 다릅니다.")
    if not vector_close(reference["normal"], other["normal"]):
        raise ValueError("리시버 수광 방향이 다릅니다.")
    if not close(reference["acceptance_angle_deg"], other["acceptance_angle_deg"]):
        raise ValueError("리시버 수광 허용각이 다릅니다.")
    # Physical axes must differ only by a signed permutation. Different tilt is
    # a different setup, even if an interpolation could produce similar values.
    transform = [[dot(axis, target) for axis in (reference["u_axis"], reference["v_axis"])]
                 for target in (other["u_axis"], other["v_axis"])]
    rounded = [[round(x) for x in row] for row in transform]
    if (any(abs(x - y) > 1e-6 for row, rr in zip(transform, rounded) for x, y in zip(row, rr))
            or any(sum(abs(x) for x in row) != 1 for row in rounded)
            or any(sum(abs(rounded[j][i]) for j in range(2)) != 1 for i in range(2))):
        raise ValueError("리시버 격자 축 방향이 다릅니다.")
    output, used = [], set()
    for iy, (v0, v1) in enumerate(zip(rv, rv[1:])):
        row = []
        for ix, (u0, u1) in enumerate(zip(ru, ru[1:])):
            corners = [(rounded[0][0] * u + rounded[0][1] * v,
                        rounded[1][0] * u + rounded[1][1] * v)
                       for u in (u0, u1) for v in (v0, v1)]
            low_u, high_u = min(p[0] for p in corners), max(p[0] for p in corners)
            low_v, high_v = min(p[1] for p in corners), max(p[1] for p in corners)
            jx, jy = bisect_right(ou, (low_u + high_u) / 2) - 1, bisect_right(ov, (low_v + high_v) / 2) - 1
            if not (0 <= jx < len(ou) - 1 and 0 <= jy < len(ov) - 1):
                raise ValueError("리시버 크기 또는 격자 좌표 범위가 다릅니다.")
            if not all(close(a, b, position=True) for a, b in ((low_u, ou[jx]), (high_u, ou[jx+1]), (low_v, ov[jy]), (high_v, ov[jy+1]))):
                raise ValueError("셀 경계 좌표가 달라 비교할 수 없습니다.")
            if (jy, jx) in used:
                raise ValueError("셀 좌표를 일대일로 대응시킬 수 없습니다.")
            used.add((jy, jx))
            row.append(other["flux_lm"][jy][jx])
        output.append(row)
    return output


def _selection(dataset: Any, receiver_id: Any) -> tuple[dict | None, list[dict]]:
    if not isinstance(dataset, dict) or dataset.get("schema") != "lt-validation.v1":
        return None, [issue("missing_input", "비교 파일을 등록해야 합니다.")]
    errors = _issues(dataset.get("issues"))
    receivers = dataset.get("receivers")
    if not isinstance(receivers, list):
        return None, errors + [issue("missing_receiver", "리시버 정보가 없습니다.")]
    matches = [r for r in receivers if isinstance(r, dict) and r.get("id") == receiver_id]
    if len(matches) != 1:
        return None, errors + [issue("missing_receiver", "비교할 리시버를 선택해야 합니다.")]
    return matches[0], errors + _issues(matches[0].get("issues"))


def _issues(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [issue(str(i.get("code", "import_issue")), str(i.get("message", "입력 데이터 확인이 필요합니다.")),
                  "warning" if i.get("severity") == "warning" else "error") for i in value if isinstance(i, dict)]


def _summary(dataset: Any, receiver_id: Any) -> tuple[dict | None, float | None, list[dict]]:
    receiver, errors = _selection(dataset, receiver_id)
    if receiver is None:
        return None, None, errors
    total, grid_errors = valid_grid(receiver)
    errors += grid_errors + _receiver_errors(receiver) + _source_errors(dataset.get("sources"))
    # An independently stored photometric total remains visible even without a
    # usable mesh. It cannot enable a pair comparison or an unverified ratio.
    if total is None:
        stored = receiver.get("total_flux_lm")
        if number(stored) and stored >= 0 and receiver.get("quantity") == "photometric":
            total = stored
    if receiver.get("quantity") != "photometric":
        total = None
    return receiver, total, errors


def _blocking(issues: list[dict]) -> bool:
    return any(x["severity"] == "error" for x in issues)


def _prefixed(issues: list[dict], prefix: str) -> list[dict]:
    return [{**x, "message": prefix + x["message"]} for x in issues]


def _ratio(numerator: float, denominator: float, subtract: float = 0.) -> float | None:
    try:
        value = (numerator / denominator - subtract) * 100
        return value if isfinite(value) else None
    except (OverflowError, ZeroDivisionError):
        return None


def compare_case(case: dict) -> dict:
    result = {"id": case.get("id"), "name": str(case.get("name", "")), "status": "blocked", "issues": [],
              **{key: None for key in ("sim_total_lm", "lt_total_lm", "flux_deviation_pct", "pearson", "overlap_pct",
                                      "sim_relative_pct", "lt_relative_pct", "sim_change_pct", "lt_change_pct", "plots")}}
    sim, sim_total, sim_errors = _summary(case.get("sim"), case.get("sim_receiver_id"))
    lt, lt_total, lt_errors = _summary(case.get("lt"), case.get("lt_receiver_id"))
    result.update(sim_total_lm=sim_total, lt_total_lm=lt_total)
    result["issues"] = _prefixed(sim_errors, "Sim: ") + _prefixed(lt_errors, "LT: ")
    if _blocking(result["issues"]):
        return result
    assert sim is not None and lt is not None and sim_total is not None and lt_total is not None
    result["issues"] += _compare_sources(case["sim"]["sources"], case["lt"]["sources"])
    try:
        aligned = _align(sim, lt)
    except ValueError as exc:
        result["issues"].append(issue("receiver_mismatch", str(exc)))
        return result
    if _blocking(result["issues"]):
        return result
    if sim_total <= 0 or lt_total <= 0:
        # Matching conditions and a real zero result are still valid. Preserve
        # the defined absolute deviation (0 / LT - 1 = -100%) while leaving
        # normalized distributions undefined. Never manufacture overlap=100.
        result["status"] = "ready"
        result["flux_deviation_pct"] = _ratio(sim_total, lt_total, 1.) if lt_total > 0 else None
        result["issues"].append(issue("zero_total", "총 수광량이 0인 map은 정규화·상관·중첩률을 계산할 수 없습니다. LT 총량이 0이면 편차도 정의되지 않습니다.", "warning"))
        return result
    a = [[x / sim_total for x in row] for row in sim["flux_lm"]]
    b = [[x / lt_total for x in row] for row in aligned]
    af, bf = [x for row in a for x in row], [x for row in b for x in row]
    mean_a, mean_b = fsum(af) / len(af), fsum(bf) / len(bf)
    da, db = [x - mean_a for x in af], [x - mean_b for x in bf]
    va, vb = fsum(x*x for x in da), fsum(x*x for x in db)
    pearson = None
    if va > 0 and vb > 0:
        pearson = max(-1., min(1., fsum(x*y for x, y in zip(da, db)) / sqrt(va) / sqrt(vb)))
    else:
        result["issues"].append(issue("constant_mesh", "균일한 격자의 Pearson 상관계수는 정의되지 않습니다.", "warning"))
    row_a, row_b = [fsum(row)*100 for row in a], [fsum(row)*100 for row in b]
    col_a, col_b = [fsum(col)*100 for col in zip(*a)], [fsum(col)*100 for col in zip(*b)]
    u, v = sim["u_edges_mm"], sim["v_edges_mm"]
    def illuminance(grid):
        return [[value / ((u[ix+1]-u[ix]) * (v[iy+1]-v[iy]) * 1e-6)
                 for ix, value in enumerate(row)] for iy, row in enumerate(grid)]
    sim_lx, lt_lx = illuminance(sim["flux_lm"]), illuminance(aligned)
    if any(not isfinite(x) for grid in (sim_lx, lt_lx) for row in grid for x in row):
        result["issues"].append(issue("illuminance_overflow", "조도 계산값이 표현 범위를 초과합니다."))
        return result
    deviation = _ratio(sim_total, lt_total, 1.)
    if deviation is None:
        result["issues"].append(issue("ratio_overflow", "수광량 편차가 표현 범위를 초과하여 표시할 수 없습니다.", "warning"))
    result.update(status="ready", flux_deviation_pct=deviation,
                  pearson=pearson, overlap_pct=max(0., min(100., fsum(min(x, y) for x, y in zip(af, bf))*100)),
                  plots={"u_mm": [(x+y)/2 for x, y in zip(u, u[1:])],
                         "v_mm": [(x+y)/2 for x, y in zip(v, v[1:])], "u_edges_mm": u, "v_edges_mm": v,
                         "sim_flux_lm": sim["flux_lm"], "lt_flux_lm": aligned,
                         "sim_illuminance_lx": sim_lx, "lt_illuminance_lx": lt_lx,
                         "sim_pct": [[x*100 for x in row] for row in a],
                         "lt_pct": [[x*100 for x in row] for row in b],
                         "difference_pp": [[(x-y)*100 for x, y in zip(ar, br)] for ar, br in zip(a, b)],
                         "column_sim_pct": col_a, "column_lt_pct": col_b,
                         "column_difference_pp": [x-y for x, y in zip(col_a, col_b)],
                         "row_sim_pct": row_a, "row_lt_pct": row_b,
                         "row_difference_pp": [x-y for x, y in zip(row_a, row_b)]})
    return result


def compare_cases(payload: dict) -> dict:
    cases = payload.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 50 or any(not isinstance(c, dict) for c in cases):
        raise ValueError("비교 케이스는 1~50개여야 합니다.")
    ids = [c.get("id") for c in cases]
    if any(not isinstance(x, str) or not x for x in ids) or len(set(ids)) != len(ids):
        raise ValueError("케이스 ID가 없거나 중복됩니다.")
    baseline_id = payload.get("baseline_id")
    if baseline_id not in ids:
        raise ValueError("기준 케이스를 선택해야 합니다.")
    baseline = cases[ids.index(baseline_id)]
    results = [compare_case(case) for case in cases]
    for case, result in zip(cases, results):
        for tool in ("sim", "lt"):
            receiver, total, errors = _summary(case.get(tool), case.get(tool + "_receiver_id"))
            base_receiver, base_total, base_errors = _summary(baseline.get(tool), baseline.get(tool + "_receiver_id"))
            if _blocking(errors + base_errors) or total is None or base_total is None or base_total <= 0:
                result["issues"].append(issue("relative_unavailable", f"{tool.upper()}: 기준 대비 수광량은 유효한 원시 격자와 동일 조건이 필요합니다.", "warning"))
                continue
            source_errors = _compare_sources(case[tool]["sources"], baseline[tool]["sources"])
            try:
                _align(base_receiver, receiver)
            except ValueError as exc:
                source_errors.append(issue("relative_mismatch", str(exc)))
            if source_errors:
                result["issues"].append(issue("relative_mismatch", f"{tool.upper()}: 기준 케이스와 수광·광원 조건이 달라 상대 비교가 불가합니다.", "warning"))
                continue
            result[tool + "_relative_pct"] = _ratio(total, base_total)
            result[tool + "_change_pct"] = _ratio(total, base_total, 1.)
            if result[tool + "_relative_pct"] is None or result[tool + "_change_pct"] is None:
                result["issues"].append(issue("relative_overflow", f"{tool.upper()}: 기준 대비 비율이 표현 범위를 초과합니다.", "warning"))
    return {"cases": results, "baseline_id": baseline_id}
