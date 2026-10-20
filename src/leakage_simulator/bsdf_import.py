"""Bounded, filesystem-free import of LT's documented ASCII measured BSDF.

This is an asset/validation contract, not a scattering implementation. The
relative grid and absolute RT data are deliberately retained separately.
See docs/bsdf-import-foundation.md for supported subsets and LT references.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

MAX_FILES = 64
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 2 * MAX_TOTAL_BYTES + 65536
MAX_GRIDS = 128
MAX_GRID_CELLS = 200_000
MAX_TOTAL_CELLS = 400_000
MASTER_HEADER = "#UDOP AngGridMultiple 1.0"
RT_HEADER = "DFAT Version 1.0"


class BsdfImportError(ValueError):
    pass


def _fail(message: str) -> None:
    raise BsdfImportError(message)


def _number(text: str, context: str) -> float:
    try:
        value = float(text)
    except (ValueError, TypeError):
        _fail(f"{context}: 유효한 숫자가 아닙니다: {text!r}")
    if not math.isfinite(value):
        _fail(f"{context}: NaN/Infinity는 허용하지 않습니다.")
    return value


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.lstrip("\ufeff").splitlines() if line.strip()]


def _content_lines(text: str) -> list[str]:
    return [line for line in _lines(text) if not line.startswith("#")]


def _header(text: str) -> str:
    lines = _lines(text)
    return lines[0] if lines else ""


def _reference_name(reference: str) -> str:
    # LT's legacy '..\\name' denotes a companion in the master directory.
    # Never open a path: resolve only against user-supplied, unique basenames.
    return reference.replace("\\", "/").rsplit("/", 1)[-1]


def _parse_master(text: str) -> list[dict[str, str]]:
    if _header(text).casefold() != MASTER_HEADER.casefold():
        _fail("지원 master 헤더는 #UDOP AngGridMultiple 1.0 입니다.")
    datasets: list[dict[str, str]] = []
    allowed = {"filename", "meshformat", "meshdatatype", "raoi", "lambda", "poi", "index", "indexincident"}
    for line in _lines(text)[1:]:
        if line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z]+)\s+=\s+(.+)", line)
        if not match:
            _fail(f"Master 문법 오류 (key = value 필요): {line[:120]}")
        key, value = match[1].casefold(), match[2].strip()
        if key not in allowed:
            _fail(f"Master {key}: 이번 단계는 FileName/Polar/BSDF/RAOI 반사 데이터만 지원합니다.")
        if key == "filename":
            datasets.append({})
            if len(datasets) > MAX_GRIDS:
                _fail(f"산란 표는 최대 {MAX_GRIDS}개입니다.")
        if not datasets:
            _fail("각 데이터셋은 FileName = 항목으로 시작해야 합니다.")
        if key in datasets[-1]:
            _fail(f"Master 중복 항목: {key}")
        datasets[-1][key] = value
    if not datasets:
        _fail("Master에 산란 데이터셋이 없습니다.")
    return datasets


def _parse_grid(text: str, file_name: str) -> tuple[list[float], list[float], list[list[float]]]:
    lines = _content_lines(text)
    if not lines or not lines[0].casefold().startswith("spheremesh:"):
        _fail(f"{file_name}: SphereMesh 헤더가 없습니다.")
    parts = lines[0].split(":", 1)[1].split()
    if len(parts) != 6:
        _fail(f"{file_name}: SphereMesh에는 크기 2개와 각도 경계 4개가 필요합니다.")
    nx, ny, lon_min, lat_min, lon_max, lat_max = [_number(x, file_name) for x in parts]
    if nx != int(nx) or ny != int(ny) or nx < 2 or ny < 2 or nx * ny > MAX_GRID_CELLS:
        _fail(f"{file_name}: 격자는 최소 2×2, 최대 {MAX_GRID_CELLS}개 값이어야 합니다.")
    nx, ny = int(nx), int(ny)
    if not (0 <= lon_min < lon_max <= 360 and 0 <= lat_min < lat_max <= 90):
        _fail(f"{file_name}: Polar 범위는 longitude 0–360°, latitude 0–90°입니다.")
    if len(lines) != ny + 1:
        _fail(f"{file_name}: latitude 행 수는 {ny}개여야 합니다.")
    values: list[list[float]] = []
    for row_index, line in enumerate(lines[1:]):
        tokens = line.split()
        if len(tokens) != nx:
            _fail(f"{file_name}: {row_index + 1}행의 longitude 값 수는 {nx}개여야 합니다.")
        row = [_number(token, file_name) for token in tokens]
        if any(value < 0 for value in row):
            _fail(f"{file_name}: 산란 값은 음수일 수 없습니다.")
        values.append(row)
    if not any(value > 0 for row in values for value in row):
        _fail(f"{file_name}: 모든 값이 0인 분포는 정규화할 수 없습니다.")
    longitudes = [lon_min + (lon_max - lon_min) * i / (nx - 1) for i in range(nx)]
    # LT's diagram orders rows from max latitude (horizon) to min (normal).
    latitudes = [lat_max - (lat_max - lat_min) * i / (ny - 1) for i in range(ny)]
    if lon_min == 0 and lon_max == 360:
        if any(not math.isclose(row[0], row[-1], rel_tol=1e-5, abs_tol=1e-12) for row in values):
            _fail(f"{file_name}: 같은 방향인 longitude 0°/360° 경계 값이 다릅니다.")
    if lat_min == 0:
        pole = values[-1]
        if any(not math.isclose(value, pole[0], rel_tol=1e-5, abs_tol=1e-12) for value in pole):
            _fail(f"{file_name}: latitude 0° 법선 방향의 값은 longitude와 무관해야 합니다.")
    return longitudes, latitudes, values


def _parse_rt(text: str) -> tuple[list[dict[str, float]], bool]:
    lines = _content_lines(text)
    if not lines or lines[0].casefold() != RT_HEADER.casefold():
        _fail("RT 헤더는 DFAT Version 1.0 이어야 합니다.")
    headers: dict[str, str] = {}
    wavelength: float | None = None
    rows: list[dict[str, float]] = []
    seen: set[tuple[float, float]] = set()
    for line in lines[1:]:
        if ":" in line:
            key, value = [token.strip() for token in line.split(":", 1)]
            key = key.casefold()
            if key not in {"dataname", "absorbing", "index", "dataitems"} or key in headers or rows or wavelength is not None:
                _fail(f"RT에서 지원하지 않거나 중복된 헤더: {key}")
            headers[key] = value
            continue
        tokens = line.split()
        if tokens[0].casefold() == "wv" and len(tokens) == 2:
            wavelength = _number(tokens[1], "RT wavelength")
            if wavelength <= 0:
                _fail("파장은 양수 nm 값이어야 합니다.")
        elif tokens[0].casefold() == "aoi" and len(tokens) == 4 and wavelength is not None:
            angle, transmittance, reflectance = [_number(token, "RT AOI") for token in tokens[1:]]
            if not 0 <= angle <= 90:
                _fail("이번 단계의 RT는 front AOI 0–90°만 지원합니다.")
            if reflectance < 0 or transmittance < 0 or reflectance > 1 or transmittance > 1:
                _fail("RT의 R/T는 0–1 범위의 비율이어야 합니다.")
            if transmittance != 0:
                _fail("이번 단계는 반사 전용입니다. T가 0이 아닌 RT는 아직 지원하지 않습니다.")
            if (wavelength, angle) in seen:
                _fail("RT에 중복된 파장/AOI가 있습니다.")
            seen.add((wavelength, angle))
            rows.append({"wavelengthNm": wavelength, "incidenceAngleDeg": angle,
                         "reflectance": reflectance, "transmittance": transmittance})
        else:
            _fail(f"RT 문법 오류: {line[:120]}")
    if headers.get("absorbing", "").upper() not in {"YES", "NO"}:
        _fail("RT에는 ABSORBING: YES 또는 NO가 필요합니다.")
    if headers.get("dataitems", "").upper().split() != ["TAVG", "RAVG"]:
        _fail("이번 단계의 RT 열 순서는 DATAITEMS: TAVG RAVG 입니다.")
    if _number(headers.get("index", "1"), "RT INDEX") != 1:
        _fail("이번 단계는 INDEX = 1인 공기 중 데이터만 지원합니다.")
    if not rows:
        _fail("RT에 파장별 AOI 데이터가 없습니다.")
    normalized = headers["absorbing"].upper() == "NO"
    for row in rows:
        total = row["reflectance"] + row["transmittance"]
        if normalized:
            if total <= 0:
                _fail("ABSORBING: NO에서 R+T가 0이면 정규화할 수 없습니다.")
            row["reflectance"] /= total
            row["transmittance"] /= total
        elif total > 1:
            _fail("ABSORBING: YES에서 R+T는 1 이하이어야 합니다.")
    return sorted(rows, key=lambda row: (row["wavelengthNm"], row["incidenceAngleDeg"])), normalized


def import_lt_ascii_bsdf(payload: Any) -> dict[str, Any]:
    """Parse selected text files only; never follow embedded filesystem paths."""
    if not isinstance(payload, dict) or set(payload) != {"files"}:
        _fail("files 목록을 가진 JSON 요청이 필요합니다.")
    supplied = payload["files"]
    if not isinstance(supplied, list) or not 3 <= len(supplied) <= MAX_FILES:
        _fail(f"Master, 산란 표, RT 파일을 함께 선택하세요 (3–{MAX_FILES}개). 단일 .bsdf는 아직 지원하지 않습니다.")
    files: dict[str, dict[str, str]] = {}
    total_bytes = 0
    for item in supplied:
        if not isinstance(item, dict) or set(item) != {"name", "text"}:
            _fail("각 파일에는 name과 text만 필요합니다.")
        name, text = item["name"], item["text"]
        if not isinstance(name, str) or not name.strip() or len(name) > 255 or re.search(r'[\\/\x00-\x1f]', name):
            _fail("업로드 이름은 경로가 없는 파일명이어야 합니다.")
        if not isinstance(text, str) or "\x00" in text:
            _fail(f"{name}: 텍스트 형식이 필요합니다. 바이너리 .bsdf는 아직 지원하지 않습니다.")
        try:
            name.encode("utf-8")
        except UnicodeEncodeError:
            _fail("파일 이름에 UTF-8로 표현할 수 없는 문자가 있습니다.")
        try:
            byte_size = len(text.encode("utf-8"))
        except UnicodeEncodeError:
            _fail(f"{name}: UTF-8로 표현할 수 없는 텍스트입니다.")
        total_bytes += byte_size
        if byte_size > MAX_FILE_BYTES or total_bytes > MAX_TOTAL_BYTES:
            _fail("BSDF 입력 한도는 파일당 2 MiB, 전체 8 MiB입니다.")
        if name.casefold() in files:
            _fail(f"중복 파일명: {name}. 폴더가 다른 동명 파일도 구분해서 선택하세요.")
        files[name.casefold()] = {"name": name, "text": text}
    masters = [item for item in files.values() if _header(item["text"]).casefold() == MASTER_HEADER.casefold()]
    rt_files = [item for item in files.values() if _header(item["text"]).casefold() == RT_HEADER.casefold()]
    if len(masters) != 1 or len(rt_files) != 1:
        _fail("2D ASCII master(#UDOP AngGridMultiple 1.0)와 RT(DFAT Version 1.0)가 각각 1개 필요합니다. 단일 .bsdf/1D slice 형식은 아직 지원하지 않습니다.")
    master, rt_file = masters[0], rt_files[0]
    datasets = _parse_master(master["text"])
    rt, normalized = _parse_rt(rt_file["text"])
    grids = []
    seen = set()
    used = {master["name"].casefold(), rt_file["name"].casefold()}
    total_cells = 0
    for dataset in datasets:
        if dataset.get("meshformat", "").casefold() != "polar" or dataset.get("meshdatatype", "").casefold() != "bsdf":
            _fail("이번 단계는 MeshFormat = Polar, MeshDataType = BSDF만 지원합니다 (Cartesian/CCBSDF 미지원).")
        if "raoi" not in dataset:
            _fail("반사 표에는 RAOI가 필요합니다.")
        angle = _number(dataset["raoi"], "RAOI")
        wavelength = _number(dataset.get("lambda", "550"), "Lambda")
        poi = _number(dataset.get("poi", "0"), "POI")
        if not 0 <= angle < 90:
            _fail("이번 단계는 front RAOI 0° 이상 90° 미만만 지원합니다. back 데이터는 아직 지원하지 않습니다.")
        if wavelength <= 0 or poi != 0 or any(_number(dataset.get(key, "1"), key) != 1 for key in ("index", "indexincident")):
            _fail("파장은 양수여야 하며, 이번 단계는 POI = 0, Index/IndexIncident = 1만 지원합니다.")
        if (wavelength, angle) in seen:
            _fail("Master에 중복된 파장/RAOI가 있습니다.")
        seen.add((wavelength, angle))
        name = _reference_name(dataset["filename"]).casefold()
        if name not in files:
            _fail(f"참조된 산란 파일이 누락되었습니다: {dataset['filename']}. 함께 선택하세요.")
        grid_file = files[name]
        lon, lat, values = _parse_grid(grid_file["text"], grid_file["name"])
        total_cells += len(lon) * len(lat)
        if total_cells > MAX_TOTAL_CELLS:
            _fail(f"전체 산란 값은 최대 {MAX_TOTAL_CELLS}개입니다.")
        rt_angles = [row["incidenceAngleDeg"] for row in rt if row["wavelengthNm"] == wavelength]
        if not rt_angles or not min(rt_angles) <= angle <= max(rt_angles):
            _fail(f"파장 {wavelength:g} nm / RAOI {angle:g}°를 포함하는 RT 데이터가 필요합니다.")
        grids.append({"fileName": grid_file["name"], "wavelengthNm": wavelength,
                      "incidenceAngleDeg": angle, "planeOfIncidenceDeg": poi, "side": "front",
                      "longitudesDeg": lon, "latitudesDeg": lat, "values": values})
        used.add(name)
    if set(files) != used:
        _fail("선택한 파일 중 master에서 참조하지 않는 파일이 있습니다: " + ", ".join(files[name]["name"] for name in sorted(set(files) - used)))
    wavelengths = sorted({grid["wavelengthNm"] for grid in grids})
    if {row["wavelengthNm"] for row in rt} != set(wavelengths):
        _fail("Master와 RT의 파장 목록이 일치해야 합니다.")
    sources = sorted(files.values(), key=lambda item: item["name"].casefold())
    digest = hashlib.sha256(json.dumps(sources, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    warnings = ["분포 표는 상대 BSDF 모양이며 총 반사율은 RT 데이터입니다. POI=0만으로 등방성을 확정하지 않습니다."]
    if normalized:
        warnings.append("ABSORBING: NO 규칙에 따라 RT의 R+T를 1로 정규화했습니다. 원본은 그대로 보관합니다.")
    return {"id": f"bsdf-{digest}", "format": "lighttools-ascii-bsdf-v1", "parserVersion": 1,
            "files": sources, "data": {"symmetry": "unspecified", "meshFormat": "polar", "valueType": "bsdf_relative",
                                      "wavelengthsNm": wavelengths, "incidenceAnglesDeg": sorted({grid['incidenceAngleDeg'] for grid in grids}),
                                      "grids": grids, "rt": rt, "refractiveIndex": 1},
            "warnings": warnings, "simulationReady": False}
