from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .comparison import compare_cases, number, vector

router = APIRouter(prefix="/api/lt-validation", tags=["LT validation"])
MAX_JSON = 128 * 1024**2


def _invalid_constant(value: str):
    raise ValueError("NaN/Infinity는 입력할 수 없습니다.")


def _finite_float(value: str):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("수치가 표현 범위를 초과합니다.")
    return result


def _issue_list(value: dict):
    value.setdefault("issues", [])
    if not isinstance(value["issues"], list) or any(
        not isinstance(i, dict) or not isinstance(i.get("code"), str)
        or not isinstance(i.get("message"), str) or i.get("severity") not in ("warning", "error")
        for i in value["issues"]
    ):
        raise ValueError("진단 메시지 형식이 올바르지 않습니다.")


def _coordinates(value: dict):
    for key in ("center_mm", "u_axis", "v_axis", "normal"):
        value.setdefault(key, None)
        if value[key] is not None and not vector(value[key]):
            raise ValueError(key + ": 3개 유한한 숫자 또는 null이 필요합니다.")


def _numeric_fields(value: dict, fields: tuple):
    for key in fields:
        value.setdefault(key, None)
        if value[key] is not None and not number(value[key]):
            raise ValueError(key + ": 유한한 숫자 또는 null이 필요합니다.")


def read_canonical(path: Path, filename: str) -> dict:
    if path.stat().st_size > MAX_JSON:
        raise ValueError("LT JSON 파일이 크기 제한을 초과합니다.")
    value = json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=_invalid_constant, parse_float=_finite_float)
    if (not isinstance(value, dict) or value.get("schema") != "lt-validation.v1"
            or value.get("kind") not in ("bitsam", "lighttools", "manual")
            or not isinstance(value.get("receivers"), list) or not isinstance(value.get("sources"), list)):
        raise ValueError("lt-validation.v1 형식의 LT 데이터가 필요합니다.")
    if len(value["receivers"]) > 1000 or len(value["sources"]) > 1000:
        raise ValueError("리시버 또는 광원 수가 제한을 초과합니다.")
    if any(not isinstance(r, dict) or not isinstance(r.get("id"), str) for r in value["receivers"]):
        raise ValueError("리시버 ID 형식이 올바르지 않습니다.")
    ids = [r["id"] for r in value["receivers"]]
    if len(set(ids)) != len(ids):
        raise ValueError("리시버 ID가 중복됩니다.")
    _issue_list(value)
    for receiver in value["receivers"]:
        receiver.setdefault("name", receiver["id"])
        if not receiver["id"] or not isinstance(receiver["name"], str):
            raise ValueError("리시버 이름 또는 ID가 올바르지 않습니다.")
        _issue_list(receiver)
        _coordinates(receiver)
        _numeric_fields(receiver, ("total_flux_lm", "acceptance_angle_deg"))
        receiver.setdefault("quantity", "unknown")
        receiver.setdefault("raw", None)
        receiver.setdefault("aperture", None)
        if receiver["quantity"] not in ("photometric", "radiometric", "unknown"):
            raise ValueError("수광 데이터 물리량이 올바르지 않습니다.")
        if receiver["raw"] is not None and type(receiver["raw"]) is not bool:
            raise ValueError("원시 데이터 확인 상태가 올바르지 않습니다.")
        if receiver["aperture"] not in ("none", None):
            raise ValueError("현재 형식은 Aperture 없음 또는 미확인만 지원합니다.")
        for key in ("u_edges_mm", "v_edges_mm"):
            receiver.setdefault(key, None)
            if receiver[key] is not None and (not isinstance(receiver[key], list) or not all(number(x) for x in receiver[key])):
                raise ValueError("셀 경계는 유한한 숫자 배열이어야 합니다.")
        receiver.setdefault("flux_lm", None)
        flux = receiver["flux_lm"]
        if flux is not None and (not isinstance(flux, list) or any(
            not isinstance(row, list) or any(not number(x) for x in row) for row in flux
        )):
            raise ValueError("셀 수광량은 유한한 숫자의 2차원 배열이어야 합니다.")
    for source in value["sources"]:
        if not isinstance(source, dict) or not isinstance(source.get("id"), str):
            raise ValueError("광원 정보 또는 ID가 올바르지 않습니다.")
        source.setdefault("name", source["id"])
        if not isinstance(source["name"], str):
            raise ValueError("광원 이름이 올바르지 않습니다.")
        _coordinates(source)
        _numeric_fields(source, ("total_flux_lm", "width_mm", "height_mm"))
        source.setdefault("flux_reference", None)
        source.setdefault("angular_distribution", None)
        source.setdefault("aim", None)
        if source["flux_reference"] not in ("emitted", "aim_region", None):
            raise ValueError("광원 광속 기준이 올바르지 않습니다.")
        if source["angular_distribution"] is not None and not isinstance(source["angular_distribution"], str):
            raise ValueError("방사 분포 형식이 올바르지 않습니다.")
        aim = source["aim"]
        if aim is not None:
            if not isinstance(aim, dict):
                raise ValueError("Aim 설정은 객체 또는 null이어야 합니다.")
            # The planar Aim normal is implied by U/V; do not invent it.
            for key in ("center_mm", "u_axis", "v_axis"):
                aim.setdefault(key, None)
                if aim[key] is not None and not vector(aim[key]):
                    raise ValueError("Aim 좌표는 3개 유한한 숫자여야 합니다.")
            _numeric_fields(aim, ("width_mm", "height_mm"))
            aim.setdefault("distribution", None)
            if aim["distribution"] is not None and not isinstance(aim["distribution"], str):
                raise ValueError("Aim 분포 형식이 올바르지 않습니다.")
    value["filename"] = filename
    return value


def _import_file(path: Path, filename: str) -> dict:
    suffix = Path(filename).suffix.lower()
    if suffix == ".bitsam":
        from .bitsam import read_bitsam
        return read_bitsam(path, filename)
    if suffix == ".lts":
        from .lighttools import read_lts
        return read_lts(path, filename)
    if suffix == ".json":
        return read_canonical(path, filename)
    raise ValueError(".bitsam, .lts 또는 LT 데이터 .json 파일을 선택하세요.")


@router.post("/import")
async def import_result(request: Request, filename: str = ""):
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    suffix = Path(filename).suffix.lower()
    if suffix not in (".bitsam", ".lts", ".json"):
        return JSONResponse({"error": ".bitsam, .lts 또는 .json 파일이 필요합니다."}, status_code=400)
    limit = 8 * 1024**3 if suffix == ".bitsam" else MAX_JSON
    # Only the temporary uploaded bytes are written. No CAD/scene cache, live
    # project, or running analysis is read or modified by these routes.
    try:
        with tempfile.TemporaryDirectory(prefix="bitsam_lt_") as temporary:
            path = Path(temporary) / ("input" + suffix)
            with path.open("wb") as stream:
                size = 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        return JSONResponse({"error": "파일 크기 제한을 초과합니다."}, status_code=413)
                    await run_in_threadpool(stream.write, chunk)
            return await run_in_threadpool(_import_file, path, filename)
    except (ValueError, TypeError, KeyError, OSError, UnicodeError, RecursionError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/compare")
async def compare(request: Request):
    try:
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_JSON:
                return JSONResponse({"error": "비교 데이터가 크기 제한을 초과합니다."}, status_code=413)
        value = json.loads(content, parse_constant=_invalid_constant, parse_float=_finite_float)
        if not isinstance(value, dict):
            raise ValueError("비교 데이터 형식이 올바르지 않습니다.")
        return await run_in_threadpool(compare_cases, value)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
