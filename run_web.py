from __future__ import annotations

import html
import json
import re
import sys
import os
import threading
import time
import traceback
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent
sys.path.append(str(ROOT / "src"))

DESKTOP_BOOT = os.environ.get("LEAKAGE_DESKTOP_BOOT", "").strip() == "1"
if DESKTOP_BOOT:
    print("[BOOT] Embedded Python runtime started.", flush=True)
    print("[BOOT] Loading simulator modules...", flush=True)

from leakage_simulator.engine import execute_run
from leakage_simulator.materials import default_material_library
from leakage_simulator.raytrace_bridge import build_direct_trace_input
from leakage_simulator.raytracer import run_direct_ray_trace
from leakage_simulator.roi import build_scene_payload
from leakage_simulator.types import EmitterConfig, GapRule, RunConfig

WEB_UI_VERSION = "0.9.15"
OUTPUT_FILE_INDEX: Dict[str, Path] = {}
SCENE_MESH_CACHE: Dict[str, Dict] = {}
RAYTRACE_JOBS: Dict[str, Dict] = {}
RAYTRACE_JOBS_LOCK = threading.Lock()
UPLOAD_DIR = ROOT / "_uploads"
DEMO_CAD_PATH = ROOT / "samples" / "tv_leakage_full_assembled_no_gap.stp"
STATIC_DIR = ROOT / "web" / "static"
SERVER_BOOT_TOKEN = str(time.time_ns())

if DESKTOP_BOOT:
    print("[BOOT] Simulator modules loaded. CAD runtime will load on STEP import.", flush=True)


def _cache_scene_mesh(scene_payload: Dict) -> str:
    scene_token = "scene_{}".format(time.time_ns())
    SCENE_MESH_CACHE[scene_token] = scene_payload["mesh"]
    while len(SCENE_MESH_CACHE) > 3:
        oldest_token = next(iter(SCENE_MESH_CACHE))
        SCENE_MESH_CACHE.pop(oldest_token, None)
    return scene_token


def _update_raytrace_job(job_id: str, **updates) -> None:
    with RAYTRACE_JOBS_LOCK:
        job = RAYTRACE_JOBS.get(job_id)
        if job is not None:
            job.update(updates)


def _raytrace_job_snapshot(job_id: str) -> Optional[Dict]:
    with RAYTRACE_JOBS_LOCK:
        job = RAYTRACE_JOBS.get(job_id)
        if job is None:
            return None
        return dict(job)


def _prune_raytrace_jobs(max_jobs: int = 8) -> None:
    with RAYTRACE_JOBS_LOCK:
        if len(RAYTRACE_JOBS) <= max_jobs:
            return
        removable = sorted(
            (
                (job_id, float(job.get("created_at", 0.0)))
                for job_id, job in RAYTRACE_JOBS.items()
                if job.get("status") in {"completed", "failed"}
            ),
            key=lambda item: item[1],
        )
        for job_id, _ in removable:
            if len(RAYTRACE_JOBS) <= max_jobs:
                break
            RAYTRACE_JOBS.pop(job_id, None)


def _run_raytrace_job(job_id: str, scene_mesh: Dict, request_payload: Dict) -> None:
    try:
        _update_raytrace_job(job_id, status="running", phase="preparing")
        trace_input = build_direct_trace_input(scene_mesh, request_payload)
        total_ray_count = sum(
            emitter.ray_count for emitter in trace_input.emitters if emitter.enabled
        )
        trace_started_at = time.time()
        _update_raytrace_job(
            job_id,
            phase="tracing",
            processed_rays=0,
            total_rays=total_ray_count,
            progress=0.0,
            elapsed_sec=0.0,
            estimated_remaining_sec=None,
        )

        def report_progress(processed_rays: int, total_rays: int) -> None:
            elapsed_sec = max(0.0, time.time() - trace_started_at)
            safe_total = max(0, int(total_rays))
            safe_processed = max(0, min(int(processed_rays), safe_total))
            progress = safe_processed / safe_total if safe_total > 0 else 0.0
            if safe_processed > 0 and elapsed_sec > 0.0:
                ray_rate = safe_processed / elapsed_sec
                estimated_remaining_sec = (
                    max(0.0, safe_total - safe_processed) / ray_rate
                    if ray_rate > 0.0
                    else None
                )
            else:
                ray_rate = 0.0
                estimated_remaining_sec = None
            _update_raytrace_job(
                job_id,
                phase="tracing",
                processed_rays=safe_processed,
                total_rays=safe_total,
                progress=progress,
                elapsed_sec=elapsed_sec,
                estimated_remaining_sec=estimated_remaining_sec,
                rays_per_sec=ray_rate,
            )

        result = run_direct_ray_trace(trace_input, progress_callback=report_progress)
        _update_raytrace_job(
            job_id,
            status="completed",
            phase="completed",
            processed_rays=total_ray_count,
            total_rays=total_ray_count,
            progress=1.0,
            elapsed_sec=max(0.0, time.time() - trace_started_at),
            estimated_remaining_sec=0.0,
            result=result.to_dict(),
            completed_at=time.time(),
        )
    except Exception as exc:
        _update_raytrace_job(
            job_id,
            status="failed",
            phase="failed",
            error=str(exc),
            estimated_remaining_sec=None,
            completed_at=time.time(),
        )


def _safe_upload_filename(raw_name: str) -> Optional[str]:
    base_name = Path(raw_name or "").name.strip()
    if not base_name:
        return None
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", base_name)
    lower_name = normalized.lower()
    allowed_suffixes = (".obj", ".stl", ".stp", ".step", ".x_t")
    if not any(lower_name.endswith(suffix) for suffix in allowed_suffixes):
        return None
    return normalized


def _prepare_upload_path(raw_name: str) -> Tuple[Path, str]:
    safe_name = _safe_upload_filename(raw_name)
    if safe_name is None:
        raise ValueError("Supported CAD formats: .obj, .stl, .stp, .step, .x_t")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    candidate = UPLOAD_DIR / safe_name
    stem = candidate.stem
    suffix = candidate.suffix
    index = 1
    while candidate.exists():
        candidate = UPLOAD_DIR / "{}_{}{}".format(stem, index, suffix)
        index += 1
    return candidate, safe_name


def _parse_int(raw: str, default: Optional[int] = None) -> Optional[int]:
    raw = (raw or "").strip()
    if not raw:
        return default
    return int(raw)


def _parse_float(raw: str, default: Optional[float] = None) -> Optional[float]:
    raw = (raw or "").strip()
    if not raw:
        return default
    return float(raw)


def _parse_int_list(raw: str) -> Optional[List[int]]:
    raw = (raw or "").strip()
    if not raw:
        return None
    return [int(v.strip()) for v in raw.split(",") if v.strip()]


def _parse_tuple(raw: str) -> Optional[Tuple[float, float, float]]:
    raw = (raw or "").strip()
    if not raw:
        return None
    vals = [float(v.strip()) for v in raw.split(",") if v.strip()]
    if len(vals) != 3:
        raise ValueError("tuple requires x,y,z")
    return vals[0], vals[1], vals[2]


def _safe_output_name(name: str) -> Optional[str]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        return None
    return name


def _safe_static_path(raw_path: str) -> Optional[Path]:
    normalized = urllib.parse.unquote(raw_path or "").replace("\\", "/").strip("/")
    if not normalized or ".." in normalized.split("/"):
        return None
    candidate = (STATIC_DIR / normalized).resolve()
    static_root = STATIC_DIR.resolve()
    try:
        candidate.relative_to(static_root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def _static_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".js":
        return "text/javascript; charset=utf-8"
    if suffix == ".css":
        return "text/css; charset=utf-8"
    if suffix == ".json":
        return "application/json; charset=utf-8"
    if suffix == ".wasm":
        return "application/wasm"
    return "application/octet-stream"


def _material_library_options() -> str:
    return "".join(
        [
            f'<option value="{value}"{" selected" if value == "black_pc_resin" else ""}>{value}</option>'
            for value in sorted(default_material_library().keys())
        ]
    )


def _register_output_file(path_text: Optional[str]) -> None:
    if not path_text:
        return
    path = Path(path_text).resolve()
    if path.exists():
        OUTPUT_FILE_INDEX[path.name] = path


def _build_html_form(material_options: str, version: str) -> str:
    return f"""<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <title>Leakage Simulator Web v{version}</title>
  <style>
    :root {{
      --line: #e2e8f0;
      --muted: #64748b;
      --ink: #0f172a;
      --accent: #2563eb;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
      height: 100%;
    }}
    body {{
      margin: 0;
      background: #f1f5f9;
      color: var(--ink);
      font-family: "Segoe UI", Arial, sans-serif;
      overflow: hidden;
    }}
    .app {{
      display: grid;
      grid-template-columns: 460px 1fr;
      height: 100vh;
      min-height: 0;
      overflow: hidden;
    }}
    .panel {{
      background: white;
      border-right: 1px solid var(--line);
      padding: 16px;
      height: 100vh;
      min-height: 0;
      overflow-y: auto;
      overflow-x: hidden;
    }}
    .toolbar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 12px;
    }}
    .toolbar h1 {{ margin: 0; font-size: 20px; }}
    .sidebar-layout-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin: 12px 0;
    }}
    .sidebar-layout-label {{
      font-size: 12px;
      color: #64748b;
      font-weight: 600;
    }}
    .sidebar-nav-shell {{
      display: flex;
      flex-direction: column;
    }}
    #sideTabBar {{ order: 0; }}
    .side-tab-panel[data-side-panel='roi'] {{ order: 1; }}
    .side-tab-panel[data-side-panel='components'] {{ order: 2; }}
    .side-tab-panel[data-side-panel='raytracing'] {{ order: 3; }}
    .side-tab-panel[data-side-panel='result'] {{ order: 4; }}
    .side-tab-panel[data-side-panel='transform_manager'] {{ order: 5; }}
    .side-tab-panel[data-side-panel='material'] {{ order: 6; }}
    .sidebar-layout-toggle {{
      display: inline-flex;
      gap: 6px;
      padding: 4px;
      border-radius: 999px;
      background: #e2e8f0;
      border: 1px solid #cbd5e1;
    }}
    .layout-btn {{
      padding: 7px 12px;
      border: 0;
      border-radius: 999px;
      background: transparent;
      color: #334155;
      font-size: 12px;
      font-weight: 700;
    }}
    .layout-btn.active {{
      background: #2563eb;
      color: #ffffff;
    }}
    .sidebar-nav-shell[data-layout='vertical'] #sideTabBar {{
      display: none;
    }}
    .sidebar-nav-shell[data-layout='horizontal'] #sideTabBar {{
      display: grid;
    }}
    .sidebar-tabs {{
      grid-template-columns: repeat(3, 1fr);
      gap: 6px;
      margin-bottom: 12px;
    }}
    .side-tab-btn {{
      padding: 9px 8px;
      border: 1px solid #cbd5e1;
      border-radius: 10px;
      background: #e2e8f0;
      color: #334155;
      font-size: 12px;
      font-weight: 700;
    }}
    .side-tab-btn.active {{
      background: #2563eb;
      color: #ffffff;
      border-color: #1d4ed8;
    }}
    .side-tab-panel {{
      display: none;
    }}
    .side-tab-panel.active {{
      display: block;
    }}
    .accordion-btn {{
      width: 100%;
      display: none;
      align-items: center;
      justify-content: space-between;
      background: #e2e8f0;
      color: #1e293b;
      border: 1px solid #cbd5e1;
      border-radius: 12px;
      padding: 12px 14px;
      font-size: 14px;
      font-weight: 700;
      margin-bottom: 8px;
      transition: background 0.18s ease, border-color 0.18s ease, color 0.18s ease, box-shadow 0.18s ease;
    }}
    .accordion-btn::after {{
      content: '▾';
      color: #64748b;
      font-size: 13px;
      transition: transform 0.22s ease, color 0.18s ease;
    }}
    .accordion-btn.active {{
      background: #dbeafe;
      border-color: #93c5fd;
      color: #1d4ed8;
      box-shadow: 0 4px 14px rgba(37, 99, 235, 0.10);
    }}
    .accordion-btn.active::after {{
      color: #1d4ed8;
      transform: rotate(180deg);
    }}
    .side-panel-body {{
      display: block;
    }}
    .sidebar-nav-shell[data-layout='vertical'] .side-tab-panel {{
      display: block;
      margin-bottom: 10px;
    }}
    .sidebar-nav-shell[data-layout='vertical'] .accordion-btn {{
      display: flex;
    }}
    .sidebar-nav-shell[data-layout='vertical'] .side-panel-body {{
      overflow: hidden;
      max-height: 0;
      opacity: 0;
      transform: translateY(-4px);
      transition: max-height 0.24s ease, opacity 0.18s ease, transform 0.18s ease;
    }}
    .sidebar-nav-shell[data-layout='vertical'] .side-tab-panel.active .side-panel-body {{
      max-height: 1600px;
      opacity: 1;
      transform: translateY(0);
    }}
    .tag {{
      color: #0284c7;
      font-size: 12px;
      border: 1px solid #bae6fd;
      background: #f0f9ff;
      padding: 2px 7px;
      border-radius: 999px;
    }}
    .card {{
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-radius: 12px;
      padding: 12px;
      margin-bottom: 12px;
    }}
    .card h2 {{ margin: 0 0 10px; font-size: 16px; }}
    .step {{ color: var(--muted); margin-bottom: 8px; font-size: 12px; }}
    label {{ display: block; font-size: 13px; margin: 8px 0 4px; color: #334155; }}
    input[type='text'], input[type='number'], select {{
      width: 100%;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      padding: 8px;
      font-size: 13px;
    }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
    button {{
      background: var(--accent);
      color: white;
      border: 0;
      border-radius: 8px;
      padding: 10px 12px;
      font-weight: 600;
      cursor: pointer;
    }}
    .ghost {{ background: #334155; }}
    .run-btn {{ width: 100%; font-size: 16px; margin-top: 6px; }}
    .small {{ font-size: 12px; color: var(--muted); margin: 6px 0 0; }}
    .object-list {{
      max-height: 170px;
      overflow: auto;
      border: 1px solid #cbd5e1;
      background: white;
      border-radius: 8px;
      padding: 6px;
    }}
    .object-item {{ padding: 7px 6px; border-bottom: 1px solid #e2e8f0; font-size: 13px; }}
    .object-item:last-child {{ border-bottom: none; }}
    .object-item.is-selected {{
      background: #dbeafe;
      border-radius: 10px;
    }}
    .object-item.is-hidden .component-row-main {{
      opacity: 0.48;
    }}
    .object-item.is-hidden .component-row-main .name {{
      text-decoration: line-through;
      text-decoration-color: #94a3b8;
    }}
    .object-item.is-non-traceable {{
      border-left: 3px solid #f59e0b;
      background: #fffbeb;
    }}
    .object-item.is-non-traceable .component-row-main .meta::after {{
      content: ' · Traceability off';
      color: #b45309;
      font-weight: 700;
    }}
    .component-tree-row {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: center;
    }}
    .component-row-main {{
      min-width: 0;
      cursor: pointer;
    }}
    .component-row-main .name {{
      display: block;
      overflow: hidden;
      font-size: 12px;
      font-weight: 700;
      color: #1e293b;
      cursor: text;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .component-row-main .name:hover {{
      color: #2563eb;
      text-decoration: underline;
      text-underline-offset: 2px;
    }}
    .component-name-input {{
      width: 100%;
      box-sizing: border-box;
      border: 1px solid #60a5fa;
      border-radius: 8px;
      padding: 4px 6px;
      font-size: 12px;
      font-weight: 700;
      color: #0f172a;
      background: #eff6ff;
      outline: none;
    }}
    .component-row-main .meta {{
      margin-top: 2px;
      font-size: 11px;
      color: #64748b;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .component-row-actions {{
      display: flex;
      gap: 4px;
      flex-wrap: nowrap;
      justify-content: flex-end;
    }}
    .mini-btn {{
      padding: 6px 8px;
      border-radius: 8px;
      font-size: 11px;
      font-weight: 700;
    }}
    .mini-btn.component-material {{ background: #334155; color: #ffffff; }}
    .mini-btn.component-material:hover {{ background: #1e293b; }}
    .mini-btn.component-transform {{ background: #2563eb; color: #ffffff; }}
    .mini-btn.component-transform:hover {{ background: #1d4ed8; }}
    .mini-btn.traceability-toggle {{
      min-width: 62px;
      border: 1px solid #bfdbfe;
      background: #eff6ff;
      color: #1d4ed8;
    }}
    .mini-btn.traceability-toggle:hover {{ border-color: #60a5fa; background: #dbeafe; }}
    .object-item.is-non-traceable .mini-btn.traceability-toggle {{
      border-color: #f59e0b;
      background: #fef3c7;
      color: #92400e;
    }}
    .mini-btn.component-more {{
      width: 32px;
      min-width: 32px;
      padding: 6px 0;
      border: 1px solid #cbd5e1;
      background: #ffffff;
      color: #475569;
      font-size: 16px;
      line-height: 1;
    }}
    .mini-btn.component-more:hover {{
      border-color: #60a5fa;
      background: #eff6ff;
      color: #1d4ed8;
    }}
    .component-context-menu {{
      position: fixed;
      z-index: 120;
      width: 230px;
      overflow: hidden;
      border: 1px solid #334155;
      border-radius: 10px;
      background: rgba(15, 23, 42, 0.985);
      box-shadow: 0 18px 45px rgba(2, 6, 23, 0.48);
      color: #e2e8f0;
    }}
    .component-context-menu-head {{
      padding: 10px 12px;
      border-bottom: 1px solid #334155;
      background: #111c31;
    }}
    .component-context-menu-name {{
      overflow: hidden;
      color: #f8fafc;
      font-size: 12px;
      font-weight: 800;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .component-context-menu-status {{
      margin-top: 3px;
      color: #94a3b8;
      font-size: 10px;
    }}
    .component-context-menu-items {{ padding: 5px; }}
    .component-context-menu button {{
      width: 100%;
      display: flex;
      align-items: center;
      justify-content: space-between;
      min-height: 34px;
      padding: 7px 9px;
      border-radius: 6px;
      background: transparent;
      color: #dbeafe;
      text-align: left;
      font-size: 12px;
      font-weight: 700;
    }}
    .component-context-menu button:hover,
    .component-context-menu button:focus-visible {{
      outline: none;
      background: #1e3a5f;
      color: #ffffff;
    }}
    .component-context-menu button.danger {{ color: #fda4af; }}
    .component-context-menu button.danger:hover,
    .component-context-menu button.danger:focus-visible {{ background: #4c1d2a; color: #fecdd3; }}
    .component-context-menu-separator {{ height: 1px; margin: 5px 4px; background: #334155; }}
    .component-context-menu button.hidden-block,
    .component-context-menu-separator.hidden-block {{ display: none; }}
    .tree-actions {{
      display: flex;
      gap: 8px;
      margin: 8px 0 10px;
      flex-wrap: wrap;
    }}
    .tree-actions button {{
      flex: 1 1 0;
      min-width: 120px;
      padding: 8px 10px;
      font-size: 12px;
    }}
    .manager-toolbar {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin: 8px 0 10px;
    }}
    .manager-toolbar button {{
      padding: 8px 10px;
      font-size: 12px;
      flex: 1 1 120px;
    }}
    .manager-list {{
      max-height: 250px;
    }}
    .manager-row {{
      display: grid;
      grid-template-columns: 20px 1fr auto;
      gap: 8px;
      align-items: start;
      padding: 8px;
      border-bottom: 1px solid #e2e8f0;
      cursor: pointer;
    }}
    .manager-row:last-child {{
      border-bottom: none;
    }}
    .manager-row.active {{
      background: #dbeafe;
    }}
    .manager-row .title {{
      font-size: 12px;
      font-weight: 700;
      color: #1e293b;
    }}
    .manager-row .meta {{
      font-size: 11px;
      color: #64748b;
      margin-top: 2px;
      white-space: pre-line;
    }}
    .manager-row .toggle {{
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: 11px;
      color: #475569;
    }}
    .manager-empty {{
      padding: 10px;
      border: 1px dashed #cbd5e1;
      border-radius: 10px;
      background: #ffffff;
      color: #64748b;
      font-size: 12px;
    }}
    .hidden-block {{ display: none; }}
    .viewer-wrap {{
      position: relative;
      background: #020617;
      height: 100vh;
      min-height: 0;
      min-width: 0;
      color: #f8fafc;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }}
    .viewer-inner {{
      flex: 1;
      min-height: 0;
      padding: 12px;
      overflow: hidden;
    }}
    .viewer-head {{
      flex: 0 0 auto;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
      padding: 12px;
      border-bottom: 1px solid #1e293b;
      background: #0f172a;
    }}
    .viewer-head h3 {{ margin: 0; font-size: 16px; }}
    .viewer-tools {{
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      margin-left: auto;
    }}
    .viewer-tools label {{
      margin: 0;
      color: #cbd5e1;
      font-size: 12px;
    }}
    .viewer-tool-group {{
      display: flex;
      flex-direction: column;
      gap: 4px;
      min-width: 0;
    }}
    .viewer-tool-group.hidden-block {{
      display: none;
    }}
    .viewer-tool-group .tool-title {{
      font-size: 11px;
      color: #94a3b8;
    }}
    .ray-filter-panel {{
      margin: 10px 0;
      padding: 10px;
      border: 1px solid #dbe3ee;
      border-radius: 12px;
      background: #f8fafc;
    }}
    .ray-filter-panel-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 7px;
      color: #0f172a;
      font-size: 12px;
      font-weight: 800;
    }}
    .ray-filter-count {{
      min-width: 24px;
      padding: 2px 6px;
      border-radius: 999px;
      background: #e2e8f0;
      color: #334155;
      text-align: center;
      font-size: 10px;
    }}
    .ray-filter-row {{
      display: flex;
      align-items: center;
      gap: 9px;
      min-height: 32px;
      padding: 5px 7px;
      border-radius: 8px;
      color: #334155;
      cursor: pointer;
    }}
    .ray-filter-row:hover {{ background: #eef2f7; }}
    .ray-filter-row.receiver {{
      margin-bottom: 5px;
      background: rgba(132, 204, 22, 0.10);
      color: #3f6212;
      font-weight: 700;
    }}
    .ray-filter-row input {{ margin: 0; accent-color: #84cc16; }}
    .ray-swatch {{
      width: 24px;
      height: 3px;
      border-radius: 999px;
      background: var(--ray-color, #ffffff);
      box-shadow: 0 0 7px var(--ray-color, #ffffff);
    }}
    .ray-filter-divider {{
      height: 1px;
      margin: 5px 2px;
      background: #dbe3ee;
    }}
    .ray-filter-actions {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 6px;
      margin-top: 8px;
    }}
    .ray-filter-actions button {{
      min-width: 0;
      padding: 6px 5px;
      border: 1px solid #cbd5e1;
      border-radius: 7px;
      background: #ffffff;
      color: #475569;
      font-size: 10px;
      font-weight: 700;
      cursor: pointer;
    }}
    .ray-filter-actions button:hover {{ border-color: #60a5fa; color: #1d4ed8; }}
    .ray-filter-note {{
      margin-top: 8px;
      color: #64748b;
      font-size: 10px;
      line-height: 1.45;
    }}
    .mode-buttons {{
      display: inline-flex;
      border: 1px solid #334155;
      border-radius: 10px;
      overflow: hidden;
      background: #0b1220;
    }}
    .mode-btn {{
      min-width: 92px;
      padding: 8px 10px;
      border: 0;
      border-right: 1px solid #334155;
      border-radius: 0;
      background: transparent;
      color: #cbd5e1;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
    }}
    .mode-btn:last-child {{
      border-right: 0;
    }}
    .mode-btn.active {{
      background: #2563eb;
      color: #ffffff;
    }}
    .camera-buttons {{
      display: grid;
      grid-template-columns: repeat(4, minmax(52px, 1fr));
      gap: 6px;
    }}
    .camera-btn {{
      min-width: 0;
      padding: 7px 8px;
      border: 1px solid #334155;
      border-radius: 9px;
      background: #0b1220;
      color: #cbd5e1;
      font-size: 12px;
      font-weight: 700;
      cursor: pointer;
    }}
    .camera-btn:hover {{
      border-color: #60a5fa;
      color: #eff6ff;
    }}
    .range-wrap {{
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 6px 10px;
      border: 1px solid #334155;
      border-radius: 10px;
      background: #0b1220;
    }}
    .range-wrap input[type='range'] {{
      width: 120px;
      accent-color: #38bdf8;
    }}
    .range-value {{
      min-width: 40px;
      text-align: right;
      color: #e2e8f0;
      font-size: 12px;
      font-weight: 600;
    }}
    .mode-badge {{
      padding: 4px 9px;
      border-radius: 999px;
      border: 1px solid #1d4ed8;
      background: rgba(37, 99, 235, 0.16);
      color: #bfdbfe;
      font-size: 11px;
      font-weight: 700;
    }}
    .tip {{ font-size: 12px; color: #94a3b8; }}
    .coord-badge {{
      position: absolute;
      left: 16px;
      top: 54px;
      z-index: 8;
      width: min(430px, calc(100% - 32px));
      border-radius: 10px;
      background: rgba(15, 23, 42, 0.82);
      border: 1px solid rgba(148, 163, 184, 0.25);
      color: #e2e8f0;
      box-shadow: 0 8px 24px rgba(2, 6, 23, 0.25);
      backdrop-filter: blur(8px);
      overflow: hidden;
    }}
    .coord-badge summary {{
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
      padding: 8px 10px;
      cursor: pointer;
      list-style: none;
    }}
    .coord-badge summary::-webkit-details-marker {{
      display: none;
    }}
    .coord-badge summary::after {{
      content: '▸';
      color: #93c5fd;
      font-size: 11px;
      justify-self: end;
      grid-column: 3;
      transition: transform 0.16s ease;
    }}
    .coord-badge[open] summary::after {{
      transform: rotate(90deg);
    }}
    .coord-badge .t {{
      font-size: 11px;
      color: #93c5fd;
      font-weight: 800;
      white-space: nowrap;
    }}
    .coord-badge .s {{
      color: #cbd5e1;
      font-size: 11px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .coord-badge .v {{
      padding: 0 10px 9px;
      font-size: 11px;
      color: #cbd5e1;
      line-height: 1.45;
      white-space: pre-line;
    }}
    .move-panel {{
      position: absolute;
      right: 16px;
      top: 84px;
      z-index: 6;
      width: 290px;
      padding: 12px;
      border-radius: 12px;
      background: rgba(15, 23, 42, 0.94);
      border: 1px solid rgba(96, 165, 250, 0.32);
      color: #e2e8f0;
      box-shadow: 0 16px 32px rgba(2, 6, 23, 0.32);
    }}
    .move-panel.viewer-move-panel-disabled {{
      display: none !important;
    }}
    .move-popup {{
      position: absolute;
      z-index: 8;
      width: 260px;
      padding: 10px;
      border-radius: 12px;
      background: rgba(15, 23, 42, 0.97);
      border: 1px solid rgba(250, 204, 21, 0.4);
      color: #e2e8f0;
      box-shadow: 0 18px 36px rgba(2, 6, 23, 0.35);
      pointer-events: auto;
    }}
    .move-popup.is-dragging {{
      user-select: none;
      cursor: grabbing;
    }}
    .move-panel.hidden-block,
    .move-popup.hidden-block {{
      display: none;
    }}
    .move-title {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      margin-bottom: 8px;
      font-size: 13px;
      font-weight: 700;
    }}
    .move-popup .move-title {{
      cursor: grab;
    }}
    .move-sub {{
      color: #93c5fd;
      font-size: 11px;
      margin-bottom: 8px;
    }}
    .section-title-with-help {{
      display: flex;
      align-items: center;
      gap: 8px;
      position: relative;
    }}
    .section-title-with-help h2 {{
      margin-bottom: 6px;
    }}
    .help-tip {{
      width: 18px;
      height: 18px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 999px;
      border: 1px solid #94a3b8;
      color: #475569;
      background: #f8fafc;
      font-size: 12px;
      font-weight: 800;
      cursor: help;
    }}
    .help-popover {{
      display: none;
      position: absolute;
      left: 0;
      top: 100%;
      z-index: 20;
      width: min(340px, calc(100vw - 40px));
      padding: 10px 12px;
      border-radius: 10px;
      background: #0f172a;
      border: 1px solid #334155;
      color: #e2e8f0;
      box-shadow: 0 16px 34px rgba(15, 23, 42, 0.28);
      font-size: 12px;
      line-height: 1.45;
      white-space: normal;
    }}
    .help-tip:hover + .help-popover,
    .help-tip:focus + .help-popover {{
      display: block;
    }}
    .popup-details {{
      margin-top: 10px;
      border-radius: 10px;
      border: 1px solid rgba(148, 163, 184, 0.22);
      background: rgba(15, 23, 42, 0.58);
      overflow: hidden;
    }}
    .popup-details summary {{
      padding: 8px 10px;
      cursor: pointer;
      color: #cbd5e1;
      font-size: 12px;
      font-weight: 700;
      user-select: none;
    }}
    .popup-details .move-summary {{
      margin: 0;
      border: 0;
      border-top: 1px solid rgba(148, 163, 184, 0.18);
      border-radius: 0;
    }}
    .move-stack {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
      margin-top: 8px;
    }}
    .move-stack label {{
      margin: 0;
      font-size: 12px;
      color: #cbd5e1;
    }}
    .move-grid {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
      margin-top: 8px;
    }}
    .move-grid label {{
      margin: 0;
      font-size: 12px;
      color: #cbd5e1;
    }}
    .move-grid input {{
      width: 100%;
      margin-top: 4px;
      background: #0b1220;
      color: #f8fafc;
      border-color: #334155;
    }}
    .move-stack select {{
      width: 100%;
      margin-top: 4px;
      background: #0b1220;
      color: #f8fafc;
      border-color: #334155;
    }}
    .move-summary {{
      margin-top: 10px;
      padding: 9px 10px;
      border-radius: 10px;
      border: 1px solid rgba(148, 163, 184, 0.22);
      background: rgba(15, 23, 42, 0.65);
      color: #cbd5e1;
      font-size: 11px;
      line-height: 1.5;
      white-space: pre-line;
    }}
    .move-chip {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      border: 1px solid rgba(250, 204, 21, 0.45);
      background: rgba(250, 204, 21, 0.12);
      color: #fde68a;
      font-size: 11px;
      font-weight: 700;
    }}
    .move-close {{
      background: transparent;
      color: #cbd5e1;
      border: 1px solid #334155;
      border-radius: 8px;
      padding: 4px 8px;
      font-size: 11px;
      cursor: pointer;
    }}
    .move-actions {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
      margin-top: 10px;
    }}
    .move-actions button {{
      min-height: 34px;
      border-radius: 10px;
      border: 1px solid #334155;
      background: #172033;
      color: #e2e8f0;
      font-size: 12px;
      font-weight: 700;
      cursor: pointer;
      transition: background 140ms ease, border-color 140ms ease;
    }}
    .move-actions button:hover {{
      background: #1e293b;
      border-color: #60a5fa;
    }}
    .move-actions button.primary {{
      background: linear-gradient(135deg, #2563eb, #1d4ed8);
      border-color: rgba(96, 165, 250, 0.62);
      color: #eff6ff;
    }}
    .move-actions button.primary:hover {{
      background: linear-gradient(135deg, #3b82f6, #2563eb);
    }}
    .material-popup {{
      width: 352px;
      border-color: rgba(45, 212, 191, 0.42);
      background: rgba(8, 23, 32, 0.98);
    }}
    .material-popup .move-title {{
      margin-bottom: 10px;
    }}
    .material-popup .move-chip {{
      border-color: rgba(45, 212, 191, 0.45);
      background: rgba(45, 212, 191, 0.12);
      color: #99f6e4;
    }}
    .material-popup .move-sub {{
      color: #99f6e4;
    }}
    .material-popup .move-stack label {{
      color: #d6fbf5;
      font-weight: 700;
      font-size: 11px;
      letter-spacing: 0.01em;
    }}
    .material-popup .move-stack select {{
      background: #0b1720;
      border-color: #1f4b57;
      color: #f0fdfa;
    }}
    .material-popup .move-summary {{
      background: rgba(8, 34, 40, 0.78);
      border-color: rgba(45, 212, 191, 0.24);
      color: #c5f7ef;
    }}
    .material-popup-card {{
      border: 1px solid rgba(45, 212, 191, 0.18);
      background: rgba(9, 29, 34, 0.82);
      border-radius: 12px;
      padding: 10px;
      margin-bottom: 10px;
    }}
    .material-popup-card:last-of-type {{
      margin-bottom: 0;
    }}
    .material-popup-label {{
      font-size: 11px;
      font-weight: 800;
      color: #99f6e4;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      margin-bottom: 6px;
    }}
    .material-popup-target {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 6px;
    }}
    .material-popup-target-name {{
      font-size: 12px;
      font-weight: 700;
      color: #f0fdfa;
    }}
    .material-popup-note {{
      font-size: 11px;
      line-height: 1.45;
      color: #8dded2;
    }}
    .material-actions {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
      margin-top: 10px;
    }}
    .material-actions button {{
      min-height: 34px;
      border-radius: 10px;
      border: 1px solid #1f4b57;
      background: #10303a;
      color: #ecfeff;
      font-size: 12px;
      font-weight: 700;
      cursor: pointer;
    }}
    .material-actions button:hover {{
      background: #13414e;
      border-color: #2dd4bf;
    }}
    .material-actions button.primary {{
      background: linear-gradient(135deg, #0f766e, #0f9f92);
      border-color: rgba(94, 234, 212, 0.45);
    }}
    .material-actions button.primary:hover {{
      background: linear-gradient(135deg, #0f8b80, #14b8a6);
    }}
    .emitter-toolbar {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
      margin-top: 10px;
    }}
    .emitter-toolbar button {{
      min-height: 38px;
      border-radius: 10px;
      border: 1px solid #cbd5e1;
      background: #fff;
      color: #334155;
      font-weight: 800;
      cursor: pointer;
    }}
    .emitter-toolbar button.primary {{
      border-color: #cbd5e1;
      background: #ffffff;
      color: #334155;
    }}
    .emitter-toolbar button:hover:not(:disabled) {{
      border-color: #93c5fd;
      background: #eff6ff;
      color: #1d4ed8;
    }}
    .emitter-toolbar button:disabled {{ opacity: 0.48; cursor: default; }}
    .emitter-method-row {{
      position: relative;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      align-items: center;
    }}
    .emitter-method-row .help-tip {{
      width: 28px;
      height: 28px;
      flex: 0 0 28px;
    }}
    .emitter-method-row .help-popover {{
      left: 0;
      top: calc(100% + 4px);
      width: min(330px, calc(100vw - 48px));
    }}
    .emitter-method-row button {{ text-align: left; }}
    .emitter-method-row button.primary {{
      border-color: #cbd5e1;
      background: #ffffff;
      color: #334155;
    }}
    .emitter-method-row button.secondary {{
      border-color: #cbd5e1;
      background: #ffffff;
      color: #334155;
    }}
    .emitter-selection-banner {{
      margin-top: 10px;
      padding: 10px 11px;
      border-radius: 10px;
      border: 1px solid #dbe4f0;
      background: #f8fafc;
      color: #475569;
      font-size: 12px;
      line-height: 1.45;
    }}
    .emitter-selection-banner.active {{
      border-color: #93c5fd;
      background: #eff6ff;
      box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.08);
    }}
    .emitter-list {{ display: grid; gap: 7px; margin-top: 10px; }}
    .emitter-list-row {{
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto auto;
      gap: 9px;
      align-items: center;
      padding: 9px 10px;
      border: 1px solid #e2e8f0;
      border-radius: 10px;
      background: #fff;
      cursor: default;
    }}
    .emitter-list-row:hover,
    .emitter-list-row.active {{ border-color: #93c5fd; background: #eff6ff; }}
    .emitter-list-dot {{
      width: 11px;
      height: 11px;
      border-radius: 999px;
      background: #3b82f6;
      box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.12);
    }}
    .emitter-list-name {{ color: #0f172a; font-size: 12px; font-weight: 800; }}
    .emitter-list-meta {{ margin-top: 2px; color: #64748b; font-size: 10px; }}
    .emitter-list-power {{ color: #475569; font-size: 11px; font-weight: 800; }}
    .tree-row-actions {{ display: flex; align-items: center; gap: 4px; }}
    .tree-action-btn {{ min-height: 28px; padding: 5px 8px; border-radius: 7px; border: 1px solid #cbd5e1; background: #fff; color: #334155; font-size: 10px; font-weight: 700; }}
    .tree-action-btn:hover {{ border-color: #93c5fd; background: #eff6ff; color: #1d4ed8; }}
    .tree-action-btn.delete:hover {{ border-color: #fca5a5; background: #fff1f2; color: #be123c; }}
    .emitter-popup {{
      width: 382px;
      max-height: calc(100% - 28px);
      overflow-y: auto;
      border-color: rgba(96, 165, 250, 0.50);
      background: rgba(15, 23, 42, 0.98);
    }}
    .emitter-popup .move-sub {{ color: #93c5fd; }}
    .emitter-popup .move-chip {{
      border-color: rgba(96, 165, 250, 0.45);
      background: rgba(59, 130, 246, 0.14);
      color: #bfdbfe;
    }}
    .emitter-popup .move-stack select,
    .emitter-popup .move-stack input {{
      width: 100%;
      margin-top: 4px;
      background: #0b1220;
      color: #f8fafc;
      border-color: #334155;
    }}
    .emitter-popup .move-grid input {{ background: #0b1220; color: #f8fafc; border-color: #334155; }}
    .emitter-popup .move-actions button.primary {{
      background: #2563eb;
      border-color: #60a5fa;
    }}
    .emitter-check {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 9px;
      color: #cbd5e1;
      font-size: 12px;
      font-weight: 700;
    }}
    .emitter-check input {{ width: auto; margin: 0; }}
    .emitter-geometry-section {{
      margin-top: 9px;
      padding-top: 9px;
      border-top: 1px solid rgba(148, 163, 184, 0.22);
    }}
    .emitter-geometry-section.hidden-block {{ display: none; }}
    .reference-selection-tools {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-top: 8px;
    }}
    .reference-clear-btn {{
      width: auto;
      min-width: 148px;
      padding: 7px 10px;
      border: 1px solid #475569;
      background: rgba(71, 85, 105, 0.18);
      color: #e2e8f0;
      font-size: 11px;
      font-weight: 700;
    }}
    .receiver-popup .reference-clear-btn {{
      border-color: #64748b;
      background: rgba(71, 85, 105, 0.18);
      color: #e2e8f0;
    }}
    .reference-clear-btn:disabled {{
      opacity: 0.42;
      cursor: default;
    }}
    .reference-count {{
      color: #cbd5e1;
      font-size: 10px;
      font-weight: 700;
      text-align: right;
    }}
    .emitter-popup .field-note {{
      margin-top: 5px;
      color: #93c5fd;
      font-size: 10px;
      line-height: 1.4;
    }}
    .receiver-popup {{
      width: 382px;
      max-height: calc(100% - 28px);
      overflow-y: auto;
      border-color: rgba(96, 165, 250, 0.50);
      background: rgba(15, 23, 42, 0.98);
    }}
    .receiver-popup .move-sub {{ color: #93c5fd; }}
    .receiver-popup .move-chip {{
      border-color: rgba(96, 165, 250, 0.45);
      background: rgba(59, 130, 246, 0.14);
      color: #bfdbfe;
    }}
    .receiver-popup .move-stack select,
    .receiver-popup .move-stack input,
    .receiver-popup .move-grid input {{
      background: #0b1220;
      color: #f8fafc;
      border-color: #334155;
    }}
    .receiver-popup .move-actions button.primary {{
      background: #2563eb;
      border-color: #60a5fa;
    }}
    .receiver-popup .field-note {{
      margin-top: 5px;
      color: #93c5fd;
      font-size: 10px;
      line-height: 1.4;
    }}
    .receiver-list .emitter-list-dot {{ background: #64748b; box-shadow: 0 0 0 3px rgba(100, 116, 139, 0.14); }}
    .receiver-list .emitter-list-power {{ color: #475569; }}
    .library-tree {{
      margin-top: 12px;
      border: 1px solid #dbe4f0;
      border-radius: 12px;
      background: #f8fbff;
      overflow: hidden;
    }}
    .library-tree[open] {{
      box-shadow: 0 8px 18px rgba(15, 23, 42, 0.05);
    }}
    .library-tree summary {{
      list-style: none;
    }}
    .library-tree summary::-webkit-details-marker {{
      display: none;
    }}
    .library-tree-head {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: center;
      padding: 11px 12px;
      background: linear-gradient(180deg, #ffffff, #f8fafc);
      cursor: pointer;
      user-select: none;
    }}
    .library-tree[open] .library-tree-head {{
      border-bottom: 1px solid #e2e8f0;
    }}
    .rt-subtree {{
      margin-top: 8px;
      border: 1px solid #e2e8f0;
      border-radius: 10px;
      background: #ffffff;
      overflow: hidden;
    }}
    .rt-subtree summary {{ list-style: none; }}
    .rt-subtree summary::-webkit-details-marker {{ display: none; }}
    .rt-subtree-head {{
      display: grid;
      grid-template-columns: 1fr auto;
      align-items: center;
      gap: 8px;
      padding: 9px 10px;
      cursor: pointer;
      user-select: none;
      color: #334155;
      font-size: 12px;
      font-weight: 800;
    }}
    .rt-subtree-head::after {{ content: '›'; color: #94a3b8; transition: transform 0.18s ease; }}
    .rt-subtree[open] .rt-subtree-head {{ background: #f8fafc; border-bottom: 1px solid #e2e8f0; }}
    .rt-subtree[open] .rt-subtree-head::after {{ transform: rotate(90deg); color: #475569; }}
    .rt-subtree-body {{ padding: 10px; }}
    .panel-meta-details {{
      margin-top: 10px;
      border-top: 1px solid #e2e8f0;
      background: transparent;
    }}
    .panel-meta-details summary {{
      list-style: none;
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 9px 2px 4px;
      color: #64748b;
      font-size: 11px;
      font-weight: 700;
      cursor: pointer;
      user-select: none;
    }}
    .panel-meta-details summary::-webkit-details-marker {{ display: none; }}
    .panel-meta-details summary::before {{ content: '›'; color: #94a3b8; transition: transform 0.18s ease; }}
    .panel-meta-details[open] summary::before {{ transform: rotate(90deg); }}
    .panel-meta-details .move-summary {{ margin-top: 5px; background: #f8fafc; color: #475569; border-color: #e2e8f0; }}
    .panel-meta-details .emitter-check {{ color: #475569; }}
    .library-tree-title {{
      font-size: 13px;
      font-weight: 800;
      color: #0f172a;
      letter-spacing: 0.01em;
    }}
    .library-tree-sub {{
      margin-top: 3px;
      font-size: 11px;
      color: #475569;
    }}
    .library-tree-actions {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .library-new-btn {{
      border-radius: 999px;
      border: 1px solid #cbd5e1;
      background: #ffffff;
      color: #0f172a;
      font-size: 11px;
      font-weight: 800;
      padding: 6px 10px;
      cursor: pointer;
    }}
    .library-new-btn:hover {{
      border-color: #2dd4bf;
      color: #0f766e;
      background: #f0fdfa;
    }}
    .library-caret {{
      color: #0f172a;
      font-size: 14px;
      font-weight: 800;
      min-width: 14px;
      text-align: center;
    }}
    .library-tree[open] .library-caret {{
      transform: rotate(90deg);
    }}
    .library-tree-body {{
      padding: 12px;
      background: #ffffff;
    }}
    .library-mini {{
      color: #475569;
      font-size: 11px;
      line-height: 1.45;
      margin-bottom: 8px;
    }}
    .library-list {{
      display: grid;
      gap: 8px;
      max-height: 220px;
      overflow: auto;
    }}
    .library-row {{
      padding: 9px 10px;
      border-radius: 10px;
      border: 1px solid #e2e8f0;
      background: #ffffff;
    }}
    .library-row.active {{
      border-color: rgba(45, 212, 191, 0.7);
      background: #f0fdfa;
    }}
    .library-row .name {{
      font-size: 12px;
      font-weight: 700;
      color: #0f172a;
      margin-bottom: 4px;
    }}
    .library-row .meta {{
      white-space: pre-line;
      color: #475569;
      font-size: 11px;
      line-height: 1.45;
    }}
    .library-actions-inline {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 8px;
    }}
    .library-actions-inline button {{
      border-radius: 999px;
      border: 1px solid #334155;
      background: #172033;
      color: #e2e8f0;
      font-size: 11px;
      padding: 5px 10px;
      cursor: pointer;
    }}
    .library-actions-inline button:hover {{
      border-color: #2dd4bf;
      background: #203144;
    }}
    .assignment-list {{
      display: grid;
      gap: 8px;
      margin-top: 8px;
    }}
    .library-form {{
      margin-top: 10px;
      padding: 10px;
      border-radius: 10px;
      border: 1px solid #dbe4f0;
      background: #f8fbff;
    }}
    .library-form-head {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      margin-bottom: 8px;
      font-size: 12px;
      font-weight: 800;
      color: #0f172a;
    }}
    .library-form-actions {{
      display: flex;
      gap: 8px;
      margin-top: 10px;
      flex-wrap: wrap;
    }}
    .library-form-actions button {{
      border-radius: 10px;
      border: 1px solid #cbd5e1;
      background: #ffffff;
      color: #0f172a;
      font-size: 12px;
      font-weight: 700;
      padding: 8px 10px;
      cursor: pointer;
    }}
    .library-form-actions button.primary {{
      background: #0f766e;
      border-color: #0f766e;
      color: #f0fdfa;
    }}
    .library-form-actions button:hover {{
      border-color: #2dd4bf;
    }}
    .assignment-empty {{
      padding: 12px;
      border-radius: 10px;
      border: 1px dashed rgba(148, 163, 184, 0.26);
      color: #94a3b8;
      font-size: 11px;
      background: rgba(15, 23, 42, 0.42);
    }}
    .viewer-stage {{
      position: relative;
      height: 100%;
      min-height: 0;
    }}
    .viewer-card {{
      position: absolute;
      inset: 0;
      border: 1px solid #1e293b;
      background: #020617;
      border-radius: 12px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      box-shadow: 0 16px 40px rgba(2, 6, 23, 0.28);
      transition: all 220ms ease;
    }}
    .viewer-card-head {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 10px 12px;
      border-bottom: 1px solid #1e293b;
      background: rgba(15, 23, 42, 0.92);
      color: #e2e8f0;
      font-size: 13px;
    }}
    .viewer-card-head .mini {{
      font-size: 11px;
      color: #94a3b8;
    }}
    .viewer-canvas {{
      width: 100%;
      height: 100%;
      display: block;
      background: radial-gradient(circle at 20% 20%, #1e293b, #020617);
    }}
    .three-viewer {{
      flex: 1;
      min-height: 0;
      width: 100%;
      display: none;
      background: #020617;
    }}
    .three-viewer canvas {{
      width: 100% !important;
      height: 100% !important;
      display: block;
    }}
    .viewer-card.three-active .viewer-canvas {{
      display: none;
    }}
    .viewer-card.three-active .three-viewer {{
      display: block;
    }}
    .viewer-stage.mode-full .viewer-card-full {{
      inset: 0;
      z-index: 2;
    }}
    .viewer-stage.mode-full .viewer-card-roi {{
      inset: auto 14px 14px auto;
      width: 31%;
      min-width: 280px;
      height: 240px;
      z-index: 3;
      opacity: 0.96;
    }}
    .viewer-stage.mode-roi .viewer-card-roi {{
      inset: 0;
      z-index: 2;
    }}
    .viewer-stage.mode-roi .viewer-card-full {{
      inset: auto 14px 14px auto;
      width: 31%;
      min-width: 280px;
      height: 240px;
      z-index: 3;
      opacity: 0.98;
    }}
    .kpi {{
      display: none;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
      margin-bottom: 8px;
    }}
    .kpi .v {{
      background: #ffffff;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      padding: 8px;
    }}
    .v .label {{ font-size: 11px; color: var(--muted); }}
    .v .num {{ font-size: 16px; font-weight: 700; margin-top: 3px; }}
    .result-card {{
      border-radius: 10px;
      border: 1px solid #bfdbfe;
      background: #eff6ff;
      color: #1e3a8a;
      padding: 10px;
    }}
    .result-popup-launch {{
      width: 100%;
      margin: 10px 0 0;
      border: 1px solid #2563eb;
      border-radius: 9px;
      background: #2563eb;
      color: #ffffff;
      font-weight: 800;
    }}
    .result-popup-launch:hover:not(:disabled) {{ background: #1d4ed8; }}
    .result-popup-launch:disabled {{
      border-color: #cbd5e1;
      background: #e2e8f0;
      color: #94a3b8;
      cursor: not-allowed;
    }}
    .rt-mode-note {{ border: 1px solid #bfdbfe; background: #eff6ff; color: #1e40af; border-radius: 9px; padding: 9px 10px; font-size: 12px; line-height: 1.45; margin-top: 8px; }}
    .rt-ready-status {{ margin-top: 10px; padding: 8px 10px; border-radius: 8px; background: #f8fafc; color: #475569; font-size: 12px; }}
    .rt-ready-status.ready {{ background: #ecfdf5; color: #047857; }}
    .rt-progress-panel {{
      margin-top: 10px;
      padding: 10px;
      border: 1px solid #bfdbfe;
      border-radius: 10px;
      background: #eff6ff;
    }}
    .rt-progress-panel.failed {{ border-color: #fecaca; background: #fef2f2; }}
    .rt-progress-head, .rt-progress-meta {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }}
    .rt-progress-head {{ color: #1e3a8a; font-size: 12px; font-weight: 800; }}
    .rt-progress-panel.failed .rt-progress-head {{ color: #b91c1c; }}
    .rt-progress-track {{
      height: 10px;
      margin: 8px 0 6px;
      overflow: hidden;
      border-radius: 999px;
      background: #dbeafe;
    }}
    .rt-progress-fill {{
      width: 0%;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, #2563eb, #38bdf8);
      transition: width 0.25s ease;
    }}
    .rt-progress-panel.preparing .rt-progress-fill {{
      width: 28%;
      animation: rt-progress-preparing 1.25s ease-in-out infinite alternate;
    }}
    .rt-progress-meta {{ color: #64748b; font-size: 10px; }}
    @keyframes rt-progress-preparing {{
      from {{ transform: translateX(-85%); }}
      to {{ transform: translateX(350%); }}
    }}
    .rt-result-kpis {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; margin-bottom: 10px; }}
    .rt-result-kpi {{ background: #ffffff; border: 1px solid #dbeafe; border-radius: 8px; padding: 8px; }}
    .rt-result-kpi span {{ display: block; font-size: 10px; color: #64748b; }}
    .rt-result-kpi strong {{ display: block; margin-top: 2px; font-size: 15px; color: #0f172a; }}
    .rt-receiver-result {{ border-top: 1px solid #bfdbfe; padding-top: 10px; margin-top: 10px; }}
    .rt-receiver-title {{ font-weight: 700; color: #0f172a; margin-bottom: 7px; }}
    .rt-metric-row {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 5px; margin-bottom: 8px; }}
    .rt-metric-row div {{ background: #fff; border-radius: 7px; padding: 6px; font-size: 10px; color: #64748b; }}
    .rt-metric-row b {{ display: block; color: #1e293b; font-size: 12px; margin-top: 2px; }}
    .rt-heatmap {{ display: block; width: 100%; height: 150px; border-radius: 8px; border: 1px solid #1e293b; background: #020617; image-rendering: pixelated; }}
    .rt-result-note {{ font-size: 11px; color: #64748b; margin-top: 7px; line-height: 1.45; }}
    .ray-result-popup {{
      z-index: 12;
      width: min(780px, calc(100% - 48px));
      height: min(640px, calc(100% - 76px));
      min-width: min(520px, calc(100% - 32px));
      min-height: 360px;
      max-width: calc(100% - 32px);
      max-height: calc(100% - 76px);
      padding: 0;
      overflow: hidden;
      border-color: rgba(96, 165, 250, 0.58);
      background: rgba(8, 16, 31, 0.985);
      box-shadow: 0 28px 72px rgba(2, 6, 23, 0.62);
    }}
    .ray-result-popup .move-title {{
      min-height: 48px;
      margin: 0;
      padding: 11px 14px;
      border-bottom: 1px solid rgba(148, 163, 184, 0.22);
      background: rgba(15, 23, 42, 0.98);
    }}
    .ray-result-popup-title {{ display: flex; align-items: center; gap: 10px; }}
    .ray-result-popup-status {{
      border: 1px solid rgba(74, 222, 128, 0.38);
      border-radius: 999px;
      padding: 3px 8px;
      background: rgba(22, 101, 52, 0.20);
      color: #86efac;
      font-size: 10px;
      font-weight: 800;
    }}
    .ray-result-tabs {{
      height: 42px;
      display: flex;
      align-items: end;
      gap: 3px;
      padding: 7px 12px 0;
      overflow-x: auto;
      border-bottom: 1px solid #2b3b55;
      background: #0b1426;
      scrollbar-width: thin;
    }}
    .ray-result-tab {{
      flex: 0 0 auto;
      height: 35px;
      padding: 0 13px;
      border: 1px solid #334155;
      border-bottom-color: #2b3b55;
      border-radius: 8px 8px 0 0;
      background: #111c31;
      color: #94a3b8;
      font-size: 11px;
      font-weight: 800;
      box-shadow: none;
    }}
    .ray-result-tab:hover {{ background: #17243a; color: #e2e8f0; }}
    .ray-result-tab.active {{
      border-color: #3b82f6;
      border-bottom-color: #12213a;
      background: #12213a;
      color: #dbeafe;
    }}
    .ray-result-popup-body {{
      height: calc(100% - 90px);
      overflow-y: auto;
      padding: 16px 16px 24px;
      scrollbar-color: #475569 #0f172a;
    }}
    .ray-result-resize-handle {{
      position: absolute;
      right: 0;
      bottom: 0;
      z-index: 4;
      width: 24px;
      height: 24px;
      cursor: nwse-resize;
      touch-action: none;
    }}
    .ray-result-resize-handle::before {{
      content: '';
      position: absolute;
      right: 4px;
      bottom: 4px;
      width: 12px;
      height: 12px;
      border-right: 2px solid #60a5fa;
      border-bottom: 2px solid #60a5fa;
      opacity: 0.9;
    }}
    .ray-result-resize-handle::after {{
      content: '';
      position: absolute;
      right: 8px;
      bottom: 8px;
      width: 6px;
      height: 6px;
      border-right: 1px solid #94a3b8;
      border-bottom: 1px solid #94a3b8;
    }}
    .ray-result-popup.is-resizing {{ user-select: none; }}
    .ray-result-tab-panel {{ display: none; }}
    .ray-result-tab-panel.active {{ display: block; }}
    .ray-result-section-heading {{
      margin: 0 0 12px;
      color: #f8fafc;
      font-size: 15px;
      font-weight: 800;
    }}
    .ray-result-popup .rt-result-kpis {{ grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 9px; margin-bottom: 12px; }}
    .ray-result-popup .rt-result-kpi {{ border: 1px solid #2b3b55; background: #111c31; padding: 11px; }}
    .ray-result-popup .rt-result-kpi span {{ color: #94a3b8; }}
    .ray-result-popup .rt-result-kpi strong {{ color: #f8fafc; font-size: 17px; }}
    .ray-result-popup .rt-receiver-result {{ border: 1px solid #2b3b55; border-radius: 11px; background: #0b1528; padding: 13px; }}
    .ray-result-popup .rt-receiver-title {{ color: #f8fafc; }}
    .ray-result-popup .rt-metric-row div {{ border: 1px solid #263552; background: #111c31; color: #94a3b8; }}
    .ray-result-popup .rt-metric-row b {{ color: #f8fafc; }}
    .ray-result-popup .rt-result-note {{ color: #94a3b8; }}
    .ray-result-popup .library-row {{ border-color: #263552; background: #111c31; }}
    .ray-result-popup .library-row .name {{ color: #f8fafc; }}
    .ray-result-popup .library-row .meta {{ color: #a5b4cc; }}
    .ray-result-popup .rt-heatmap {{ height: 280px; }}
    @media (max-width: 1050px) {{
      .ray-result-popup {{ min-width: min(440px, calc(100% - 32px)); width: calc(100% - 32px); }}
      .ray-result-popup .rt-result-kpis {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
  </style>
</head>
<body>
  <div class=\"app\">
    <aside class=\"panel\">
      <div class=\"toolbar\">
        <h1>Leakage simulator</h1>
        <span class=\"tag\">Web UI v{version}</span>
      </div>
      <p class=\"small\">Workflow: Model import → ROI 설정 → Components → Transform manager → Material library → Ray tracing → Result</p>
      <form id=\"runForm\" method=\"post\" action=\"/run\">
        <div class=\"card\">
          <div class=\"step\">Step 1</div>
          <h2>Model import</h2>
          <input id=\"cadPath\" name=\"cad\" type=\"hidden\" value=\"\" />
          <input id=\"cadFilePicker\" type=\"file\" accept=\".obj,.stl,.stp,.step,.x_t\" style=\"display:none;\" />
          <label>Selected CAD file</label>
          <div class=\"grid\">
            <input id=\"cadFileName\" type=\"text\" value=\"Sample geometry (no CAD file)\" readonly />
            <button id=\"importCad\" type=\"button\" class=\"ghost\">Import CAD</button>
          </div>
          <div class=\"grid\" style=\"margin-top: 8px;\">
            <button id=\"loadDemoCad\" type=\"button\" class=\"ghost\">Load demo CAD</button>
            <button id=\"useSample\" type=\"button\" class=\"ghost\">Use sample model</button>
          </div>
          <p class=\"small\" id=\"cadMeta\">No CAD uploaded yet. Click Import CAD to choose a file from Windows.</p>
        </div>

        <div class=\"sidebar-layout-row\">
          <span class=\"sidebar-layout-label\">메뉴 배치</span>
          <div class=\"sidebar-layout-toggle\" id=\"sidebarLayoutToggle\">
            <button type=\"button\" class=\"layout-btn active\" data-layout=\"vertical\">세로형</button>
            <button type=\"button\" class=\"layout-btn\" data-layout=\"horizontal\">가로형</button>
          </div>
        </div>
        <div class=\"sidebar-nav-shell\" id=\"sidebarNavShell\" data-layout=\"vertical\">
          <div class=\"sidebar-tabs\" id=\"sideTabBar\">
            <button type=\"button\" class=\"side-tab-btn\" data-side-tab=\"roi\">ROI 설정</button>
            <button type=\"button\" class=\"side-tab-btn\" data-side-tab=\"components\">Components</button>
            <button type=\"button\" class=\"side-tab-btn\" data-side-tab=\"raytracing\">Ray tracing</button>
            <button type=\"button\" class=\"side-tab-btn\" data-side-tab=\"result\">Result</button>
            <button type=\"button\" class=\"side-tab-btn\" data-side-tab=\"transform_manager\">Transform manager</button>
            <button type=\"button\" class=\"side-tab-btn\" data-side-tab=\"material\">Material library</button>
          </div>

          <div class=\"side-tab-panel\" data-side-panel=\"roi\">
          <button type=\"button\" class=\"accordion-btn\" data-side-tab=\"roi\">ROI 설정</button>
          <div class=\"side-panel-body\">
          <div class=\"card\">
          <div class=\"step\">Step 2</div>
          <h2>ROI (target faces)</h2>
          <div class=\"grid\">
            <label>ROI 선택 방식
              <select id=\"roiSelectionMode\">
                <option value=\"none\" selected>선택 방식</option>
                <option value=\"panel\">Component 선택</option>
                <option value=\"click\">3D view에서 선택</option>
              </select>
            </label>
            <div>
              <label>&nbsp;</label>
              <button id=\"clearRoi\" type=\"button\" class=\"ghost\">ROI 초기화</button>
            </div>
          </div>
          <p class=\"small\" id=\"roiModeHint\">먼저 ROI 선택 방식을 정해주세요. 선택 전에는 3D viewer 클릭이 ROI로 반영되지 않습니다.</p>
          <div id=\"componentSelectBlock\">
            <label>Component 선택</label>
            <div id=\"objectList\" class=\"object-list\">
              <div class=\"small\">Load CAD first</div>
            </div>
          </div>
          <div id=\"faceIndexBlock\" class=\"row\">
            <label>Face index 직접 입력</label>
            <input id=\"roiFacesInput\" name=\"roi_faces\" type=\"text\" placeholder=\"ex) 10,12,25\" />
          </div>
          <p class=\"small\" id=\"roiStat\">Selected Face Count: 0</p>
        </div>
          </div>
          </div>

          <div class=\"side-tab-panel\" data-side-panel=\"components\">
          <button type=\"button\" class=\"accordion-btn\" data-side-tab=\"components\">Components</button>
          <div class=\"side-panel-body\">
          <div class=\"card\">
          <div class=\"step\">Step 3</div>
          <div class=\"section-title-with-help\">
            <h2>Assembly / Component Tree</h2>
            <span class=\"help-tip\" tabindex=\"0\" aria-label=\"Component tree help\">?</span>
            <div class=\"help-popover\">
              부품 선택과 transform 진입을 한 곳에서 처리합니다.<br>
              Component row 또는 3D 형상을 클릭하면 선택만 하고, <b>Material</b>, <b>Transform</b>, <b>Trace Off/On</b>은 행에서 바로 실행할 수 있습니다.<br>
              <b>+</b> 메뉴에는 Hide/Show와 Delete를 모았으며, 우클릭 메뉴에서는 전체 명령을 사용할 수 있습니다.<br>
              <b>Traceability Off</b>는 부품을 화면에 유지한 채 ray collision 대상에서만 제외합니다.<br>
              <b>Delete</b>는 확인 후 해당 부품을 viewer와 ray tracing 해석 대상에서 제거합니다.<br>
              <b>Transform</b> 명령을 선택할 때만 오른쪽 3D viewer의 Transform popup이 열립니다.<br>
              Transform 방식과 selection mode는 popup 안에서 설정합니다.
            </div>
          </div>
          <input id=\"gapMode\" name=\"gap_mode\" type=\"hidden\" value=\"component_move_gap\" />
          <input id=\"gapComponentIds\" name=\"gap_component_ids\" type=\"hidden\" value=\"\" />
          <input id=\"gapFaceIndices\" name=\"gap_face_indices\" type=\"hidden\" value=\"\" />
          <input id=\"gapMoveCombined\" name=\"gap_move_xyz\" type=\"hidden\" value=\"0,0,0\" />
          <input id=\"gapTiltCombined\" name=\"gap_tilt_xyz\" type=\"hidden\" value=\"0,0,0\" />
          <input name=\"gap_nominal\" type=\"hidden\" value=\"0.0\" />
          <div id=\"gapModeHint\" class=\"move-sub hidden-block\">Transform 방식과 selection mode는 오른쪽 3D viewer popup에서 설정합니다.</div>
          <label>Component Tree</label>
          <div id=\"gapObjectList\" class=\"object-list\">
            <div class=\"small\">Load CAD first</div>
          </div>
          <details class=\"panel-meta-details\">
            <summary>Information</summary>
            <div id=\"componentSelectionSummary\" class=\"move-summary\">선택된 부품 없음</div>
          </details>
        </div>
          </div>
          </div>

          <div class=\"side-tab-panel\" data-side-panel=\"transform_manager\">
          <button type=\"button\" class=\"accordion-btn\" data-side-tab=\"transform_manager\">Transform manager</button>
          <div class=\"side-panel-body\">
          <div class=\"card\">
          <div class=\"step\">Reference</div>
          <div class=\"section-title-with-help\">
            <h2>Transform Manager</h2>
            <span class=\"help-tip\" tabindex=\"0\" aria-label=\"Transform manager help\">?</span>
            <div class=\"help-popover\">
              Transform 입력은 오른쪽 3D viewer의 popup에서 수행합니다.<br>
              이 메뉴는 적용된 transform rule과 gap 해석 정보를 관리/확인하는 영역입니다.<br>
              상세 상태는 아래 <b>Information</b>을 열어 확인하세요.
            </div>
          </div>
          <div id=\"transformRulePanel\">
            <label>Applied Transform Rules</label>
            <div id=\"transformRuleList\" class=\"object-list manager-list\"></div>
            <div id=\"transformManagerEmpty\" class=\"manager-empty\">아직 등록된 component transform rule이 없습니다.</div>
          </div>
          <div id=\"gapFacePanel\" class=\"hidden-block\">
            <label>Local face target</label>
            <div class=\"move-sub\">ROI와 무관하게 전체 모델에서 local face target을 선택할 수 있습니다.</div>
            <div id=\"gapFaceSummary\" class=\"move-summary\">아직 local face target이 선택되지 않았습니다.</div>
            <div class=\"row\">
              <label>추가 face index</label>
              <input id=\"gapFaceInput\" type=\"text\" placeholder=\"ex) 10,12,25\" />
            </div>
          </div>
          <label><input id=\"previewOverlayToggle\" type=\"checkbox\" checked> 이동 전/후 preview overlay 표시</label>
          <details class=\"panel-meta-details\">
            <summary>Information</summary>
            <div id=\"transformSelectionSummary\" class=\"move-summary\">Transform rule 없음. Components 탭에서 `Transform`을 눌러 시작하세요.</div>
            <div class=\"move-summary\" id=\"gapMoveSummary\">선택된 대상 없음</div>
          </details>
          <details class=\"panel-meta-details\">
            <summary>Advanced</summary>
            <div class=\"grid\" style=\"margin-top: 8px;\">
              <div class=\"row\"><label>Gap sigma</label><input name=\"gap_sigma\" type=\"number\" step=\"0.01\" value=\"0.03\"></div>
              <div class=\"row\"><label>Gap transmissive threshold</label><input name=\"gap_transmissive_threshold\" type=\"number\" step=\"0.01\" value=\"0.4\"></div>
            </div>
            <div class=\"move-sub\" style=\"margin-top:10px;\">다음 우선순위: Save scenario A/B → Before/After compare</div>
          </details>
        </div>
          </div>
          </div>

          <div class=\"side-tab-panel\" data-side-panel=\"material\">
          <button type=\"button\" class=\"accordion-btn\" data-side-tab=\"material\">Material library</button>
          <div class=\"side-panel-body\">
          <div class=\"card\">
            <div class=\"step\">Reference</div>
            <div class=\"section-title-with-help\">
              <h2>Material Library</h2>
              <span class=\"help-tip\" tabindex=\"0\" aria-label=\"Material library help\">?</span>
              <div class=\"help-popover\">
                왼쪽 메뉴는 material library 등록/수정/업로드용입니다.<br>
                실제 부품별 material 지정은 Component의 <b>Material</b> 버튼 또는 오른쪽 3D viewer의 Material popup에서 수행합니다.<br>
                선택 대상과 적용 상태는 아래 <b>Information</b>을 열어 확인하세요.
              </div>
            </div>
            <details class=\"library-tree\">
              <summary class=\"library-tree-head\">
                <div>
                  <div class=\"library-tree-title\">Base materials</div>
                  <div class=\"library-tree-sub\">기본 재질 목록</div>
                </div>
                <div class=\"library-tree-actions\">
                  <button id=\"newMaterialBtn\" type=\"button\" class=\"library-new-btn\">New material</button>
                  <span class=\"library-caret\">›</span>
                </div>
              </summary>
              <div class=\"library-tree-body\">
                <div id=\"materialBaseList\" class=\"library-list\"></div>
                <div id=\"newMaterialForm\" class=\"library-form hidden-block\">
                  <div class=\"library-form-head\">
                    <span>New material</span>
                  </div>
                  <div class=\"grid\">
                    <label>Name<input id=\"newMaterialName\" type=\"text\" placeholder=\"ex) black_secc_custom\"></label>
                    <label>Category
                      <select id=\"newMaterialCategory\">
                        <option value=\"metal\">Metal</option>
                        <option value=\"resin\">Resin</option>
                        <option value=\"tape\">Tape</option>
                        <option value=\"foam\">Foam</option>
                      </select>
                    </label>
                    <label>Total reflectance<input id=\"newMaterialReflectance\" type=\"number\" min=\"0\" max=\"1\" step=\"0.01\" value=\"0.08\"></label>
                    <label>Default surface
                      <select id=\"newMaterialDefaultSurface\"></select>
                    </label>
                  </div>
                  <div class=\"library-form-actions\">
                    <button id=\"saveNewMaterialBtn\" type=\"button\" class=\"primary\">Save</button>
                    <button id=\"cancelNewMaterialBtn\" type=\"button\">Cancel</button>
                  </div>
                </div>
              </div>
            </details>
            <details class=\"library-tree\">
              <summary class=\"library-tree-head\">
                <div>
                  <div class=\"library-tree-title\">Surface properties</div>
                  <div class=\"library-tree-sub\">표면 반사 / 산란 특성</div>
                </div>
                <div class=\"library-tree-actions\">
                  <button id=\"newSurfaceBtn\" type=\"button\" class=\"library-new-btn\">New surface property</button>
                  <span class=\"library-caret\">›</span>
                </div>
              </summary>
              <div class=\"library-tree-body\">
                <div id=\"materialSurfaceList\" class=\"library-list\"></div>
                <div id=\"newSurfaceForm\" class=\"library-form hidden-block\">
                  <div class=\"library-form-head\">
                    <span>New surface property</span>
                  </div>
                  <div class=\"grid\">
                    <label>Name<input id=\"customSurfaceName\" type=\"text\" placeholder=\"ex) hemming_edge_low_gloss\"></label>
                    <label>Scatter model
                      <select id=\"customSurfaceScatter\">
                        <option value=\"gaussian\">Gaussian scatter</option>
                        <option value=\"lambertian\">Lambertian</option>
                        <option value=\"specular\">Specular</option>
                        <option value=\"mixed\">Mixed</option>
                      </select>
                    </label>
                    <label>Reflectance multiplier<input id=\"customSurfaceReflectance\" type=\"number\" min=\"0\" max=\"5\" step=\"0.05\" value=\"1.00\"></label>
                    <label>Specular ratio<input id=\"customSurfaceSpecular\" type=\"number\" min=\"0\" max=\"1\" step=\"0.05\" value=\"0.15\"></label>
                    <label>Diffuse / scatter ratio<input id=\"customSurfaceDiffuse\" type=\"number\" min=\"0\" max=\"1\" step=\"0.05\" value=\"0.85\"></label>
                    <label>Roughness<input id=\"customSurfaceRoughness\" type=\"number\" min=\"0\" max=\"1\" step=\"0.01\" value=\"0.70\"></label>
                    <label>Scatter width (deg)<input id=\"customSurfaceScatterWidth\" type=\"number\" min=\"0\" step=\"1\" value=\"18\"></label>
                  </div>
                  <div class=\"library-form-actions\">
                    <button id=\"registerCustomSurfaceBtn\" type=\"button\" class=\"primary\">Save</button>
                    <button id=\"cancelNewSurfaceBtn\" type=\"button\">Cancel</button>
                  </div>
                </div>
              </div>
            </details>
            <details class=\"library-tree\">
              <summary class=\"library-tree-head\">
                <div>
                  <div class=\"library-tree-title\">BSDF assets</div>
                  <div class=\"library-tree-sub\">외부 측정 파일 등록</div>
                </div>
                <div class=\"library-tree-actions\">
                  <button id=\"newBsdfBtn\" type=\"button\" class=\"library-new-btn\">New BSDF</button>
                  <span class=\"library-caret\">›</span>
                </div>
              </summary>
              <div class=\"library-tree-body\">
                <div id=\"bsdfAssetList\" class=\"library-list\"></div>
                <div id=\"newBsdfForm\" class=\"library-form hidden-block\">
                  <div class=\"library-form-head\">
                    <span>New BSDF asset</span>
                  </div>
                  <div class=\"grid\">
                    <label>Upload BSDF<input id=\"bsdfFileInput\" type=\"file\" accept=\".bsdf,.csv,.txt\"></label>
                    <label>Selected file<input id=\"bsdfFileName\" type=\"text\" value=\"No file selected\" readonly></label>
                  </div>
                  <div class=\"library-form-actions\">
                    <button id=\"registerBsdfBtn\" type=\"button\" class=\"primary\">Save</button>
                    <button id=\"cancelNewBsdfBtn\" type=\"button\">Cancel</button>
                  </div>
                </div>
              </div>
            </details>
            <details class=\"library-tree\">
              <summary class=\"library-tree-head\">
                <div>
                  <div class=\"library-tree-title\">Saved optical profiles</div>
                  <div class=\"library-tree-sub\">저장된 조합 preset</div>
                </div>
                <div class=\"library-tree-actions\">
                  <span class=\"library-caret\">›</span>
                </div>
              </summary>
              <div class=\"library-tree-body\">
                <div id=\"materialProfileList\" class=\"library-list\"></div>
              </div>
            </details>
            <details class=\"library-tree\">
              <summary class=\"library-tree-head\">
                <div>
                  <div class=\"library-tree-title\">Assignments</div>
                  <div class=\"library-tree-sub\">현재 프로젝트 적용 목록</div>
                </div>
                <div class=\"library-tree-actions\">
                  <span class=\"library-caret\">›</span>
                </div>
              </summary>
              <div class=\"library-tree-body\">
                <div id=\"materialAssignmentList\" class=\"assignment-list\"></div>
                <div id=\"materialAssignmentEmpty\" class=\"assignment-empty\">아직 적용된 material assignment가 없습니다.</div>
              </div>
            </details>
            <details class=\"panel-meta-details\">
              <summary>Information</summary>
              <div id=\"materialTargetSummary\" class=\"move-summary\">선택된 material 대상 부품 없음</div>
            </details>
          </div>
          </div>
          </div>

          <div class=\"side-tab-panel\" data-side-panel=\"raytracing\">
          <button type=\"button\" class=\"accordion-btn\" data-side-tab=\"raytracing\">Ray tracing</button>
          <div class=\"side-panel-body\">
          <div class=\"card\">
            <div class=\"step\">Step 4</div>
            <div class=\"section-title-with-help\">
              <h2>Ray tracing</h2>
              <span class=\"help-tip\" tabindex=\"0\" aria-label=\"Ray tracing help\">?</span>
              <div class=\"help-popover\">Emitter와 Receiver를 각각 설정한 뒤 ray tracing을 실행합니다. V1 Emitter는 3D viewer에서 방출 면을 직접 선택하는 방식입니다.</div>
            </div>
            <details class=\"library-tree\">
              <summary class=\"library-tree-head\">
                <div>
                  <div class=\"library-tree-title\">Emitter</div>
                  <div class=\"library-tree-sub\">광원 면 · power · 방출 방향</div>
                </div>
                <div class=\"library-tree-actions\"><span class=\"library-caret\">›</span></div>
              </summary>
              <div class=\"library-tree-body\">
                <details id=\"emitterAddSection\" class=\"rt-subtree\">
                  <summary class=\"rt-subtree-head\">Add</summary>
                  <div class=\"rt-subtree-body\">
                    <div class=\"emitter-toolbar\">
                      <div class=\"emitter-method-row\">
                        <button id=\"addFaceEmitterBtn\" type=\"button\" class=\"primary\">CAD surface</button>
                        <span class=\"help-tip\" tabindex=\"0\" aria-label=\"CAD surface emitter help\">?</span>
                        <div class=\"help-popover\">실제 CAD 부품의 surface를 발광면으로 지정합니다. 3D viewer에서 면을 클릭하고 Ctrl+클릭으로 여러 surface를 묶을 수 있습니다.</div>
                      </div>
                      <div class=\"emitter-method-row\">
                        <button id=\"addDatumEmitterBtn\" type=\"button\" class=\"secondary\">Datum plane</button>
                        <span class=\"help-tip\" tabindex=\"0\" aria-label=\"Datum plane emitter help\">?</span>
                        <div class=\"help-popover\">CAD 형상과 관계없이 빈 공간에 중심 좌표, 크기, 회전각을 입력하여 가상의 사각 발광면을 만듭니다.</div>
                      </div>
                      <div class=\"emitter-method-row\">
                        <button id=\"addReferenceEmitterBtn\" type=\"button\" class=\"secondary\">Reference geometry</button>
                        <span class=\"help-tip\" tabindex=\"0\" aria-label=\"Reference geometry emitter help\">?</span>
                        <div class=\"help-popover\">CAD의 꼭지점 3~6개 또는 모서리 2개를 선택해 그 사이 빈 공간에 가상의 발광면을 생성합니다.</div>
                      </div>
                    </div>
                    <div id=\"emitterSelectionBanner\" class=\"emitter-selection-banner\">광원이 없습니다. Add에서 생성 방식을 선택하세요.</div>
                  </div>
                </details>
                <details class=\"rt-subtree\">
                  <summary class=\"rt-subtree-head\">List</summary>
                  <div class=\"rt-subtree-body\">
                    <div id=\"emitterList\" class=\"emitter-list\"></div>
                    <div id=\"emitterEmpty\" class=\"manager-empty\">등록된 emitter가 없습니다.</div>
                    <div class=\"small\">각 emitter의 Settings 또는 Delete 버튼으로 개별 관리합니다.</div>
                  </div>
                </details>
                <input id=\"emitterSpecsJson\" name=\"emitter_specs_json\" type=\"hidden\" value=\"[]\">
                <input id=\"emitterType\" name=\"emitter_type\" type=\"hidden\" value=\"\">
                <input name=\"emitter_strength\" type=\"hidden\" value=\"1.0\">
                <input id=\"emitterDirectionDistribution\" name=\"emitter_direction_distribution\" type=\"hidden\" value=\"lambertian\">
                <input id=\"emitterFace\" name=\"emitter_face_index\" type=\"hidden\" value=\"\">
                <input id=\"emitterNormal\" name=\"emitter_normal_hint\" type=\"hidden\" value=\"\">
                <input name=\"emitter_box_min\" type=\"hidden\" value=\"\">
                <input name=\"emitter_box_max\" type=\"hidden\" value=\"\">
                <input name=\"emitter_sphere_center\" type=\"hidden\" value=\"\">
                <input name=\"emitter_sphere_radius\" type=\"hidden\" value=\"\">
                <input id=\"includeImportEmitters\" type=\"hidden\" name=\"include_import_emitters\" value=\"0\">
              </div>
            </details>
            <details class=\"library-tree\">
              <summary class=\"library-tree-head\">
                <div>
                  <div class=\"library-tree-title\">Receiver</div>
                  <div class=\"library-tree-sub\">관측 위치 · 방향 · 크기</div>
                </div>
                <div class=\"library-tree-actions\"><span class=\"library-caret\">›</span></div>
              </summary>
              <div class=\"library-tree-body\">
                <details id=\"receiverAddSection\" class=\"rt-subtree\">
                  <summary class=\"rt-subtree-head\">Add</summary>
                  <div class=\"rt-subtree-body\">
                    <div class=\"emitter-toolbar\">
                      <div class=\"emitter-method-row receiver-method-row\">
                        <button id=\"addDatumReceiverBtn\" type=\"button\" class=\"primary\">Datum plane</button>
                        <span class=\"help-tip\" tabindex=\"0\" aria-label=\"Datum plane receiver help\">?</span>
                        <div class=\"help-popover\">빈 공간에 중심 좌표, 크기, 회전각을 입력해 직사각형 측정면을 배치합니다. 휘도계 또는 사용자 관측 위치를 수치로 정의할 때 사용합니다.</div>
                      </div>
                      <div class=\"emitter-method-row receiver-method-row\">
                        <button id=\"addReferenceReceiverBtn\" type=\"button\" class=\"secondary\">Reference geometry</button>
                        <span class=\"help-tip\" tabindex=\"0\" aria-label=\"Reference geometry receiver help\">?</span>
                        <div class=\"help-popover\">CAD 꼭지점 3~6개 또는 모서리 2개를 선택해 제품 형상과 정렬된 가상 측정면을 생성합니다.</div>
                      </div>
                      <div class=\"emitter-method-row receiver-method-row\">
                        <button id=\"addCurrentViewReceiverBtn\" type=\"button\" class=\"secondary\">Current view</button>
                        <span class=\"help-tip\" tabindex=\"0\" aria-label=\"Current view receiver help\">?</span>
                        <div class=\"help-popover\">현재 3D 카메라가 바라보는 방향을 수광 normal로 사용합니다. 현재 보이는 각도에서 빛샘을 빠르게 측정할 때 사용합니다.</div>
                      </div>
                    </div>
                    <div id=\"receiverSelectionBanner\" class=\"emitter-selection-banner\">Receiver가 없습니다. Add에서 배치 방식을 선택하세요.</div>
                  </div>
                </details>
                <details class=\"rt-subtree\">
                  <summary class=\"rt-subtree-head\">List</summary>
                  <div class=\"rt-subtree-body\">
                    <div id=\"receiverList\" class=\"emitter-list receiver-list\"></div>
                    <div id=\"receiverEmpty\" class=\"emitter-empty\">등록된 receiver가 없습니다.</div>
                    <div class=\"small\">각 receiver의 Settings 또는 Delete 버튼으로 개별 관리합니다.</div>
                  </div>
                </details>
                <input id=\"receiverSpecsJson\" name=\"receiver_specs_json\" type=\"hidden\" value=\"[]\">
              </div>
            </details>
            <div id=\"directRunHint\" class=\"rt-ready-status\">Emitter와 Receiver를 각각 1개 이상 등록하세요.</div>
            <button id=\"runBtn\" class=\"run-btn\" type=\"submit\" disabled>Run ray tracing</button>
            <div id=\"rayTraceProgressPanel\" class=\"rt-progress-panel hidden-block\">
              <div class=\"rt-progress-head\">
                <span id=\"rayTraceProgressLabel\">Ray tracing 준비 중</span>
                <span id=\"rayTraceRemaining\">예상 시간 계산 중</span>
              </div>
              <div class=\"rt-progress-track\"><div id=\"rayTraceProgressFill\" class=\"rt-progress-fill\"></div></div>
              <div class=\"rt-progress-meta\">
                <span id=\"rayTraceProgressCount\">0 / 0 rays</span>
                <span id=\"rayTraceElapsed\">경과 0s</span>
              </div>
            </div>
            <details class=\"panel-meta-details\">
              <summary>Information</summary>
              <div class=\"move-summary\">Emitter: 광선이 시작되는 면과 power, 방향 분포를 정의합니다.\nReceiver: 광선이 도달하는 관측 위치와 크기를 정의합니다.</div>
            </details>
            <details class=\"panel-meta-details\">
              <summary>Advanced</summary>
              <div class=\"rt-mode-note\"><b>RT-3 multi-bounce reflection</b><br>각 충돌 face의 optical profile을 조회하고 설정한 최대 반사 횟수까지 Specular, Gaussian, Lambertian 반사를 반복합니다.</div>
              <div class=\"grid\">
                <label>Max reflections<select id=\"rtMaxDepthInput\"><option value=\"0\">0 · Direct only</option><option value=\"1\">1</option><option value=\"2\" selected>2</option><option value=\"3\">3</option></select></label>
                <label>Termination<select id=\"rtTerminationModeInput\"><option value=\"threshold\" selected>Energy threshold</option><option value=\"russian_roulette\">Russian roulette</option></select></label>
                <label>Min ray power (lm)<input id=\"rtMinEnergyInput\" type=\"number\" min=\"0\" step=\"any\" value=\"1e-9\"></label>
                <label>Result detail<select id=\"rtContributionModeInput\"><option value=\"summary\" selected>Fast summary</option><option value=\"detailed\">Detailed contribution</option></select></label>
                <label>Seed<input id=\"rtSeedInput\" name=\"seed\" type=\"number\" value=\"42\"></label>
                <label>Saved ray paths<input id=\"rtMaxPathsInput\" type=\"number\" min=\"0\" max=\"1000\" value=\"200\"></label>
                <label>Brightness scale (k_abs)<input id=\"rtKAbsInput\" name=\"k_abs\" type=\"number\" step=\"0.01\" value=\"0.12\"></label>
                <label>BRDF scale (k_brdf)<input id=\"rtKBrdfInput\" name=\"k_brdf\" type=\"number\" step=\"0.1\" value=\"1.0\"></label>
              </div>
              <label class=\"emitter-check\"><input id=\"rtStorePathsInput\" type=\"checkbox\" checked> Show hit ray paths in 3D viewer</label>
              <div class=\"small\">Fast summary는 광학 결과를 유지하면서 부품·면·소재별 상세 기여도 집계를 생략합니다. Detailed contribution은 원인 분석용 전체 집계를 저장합니다.</div>
              <div class=\"small\">Ray 수는 각 Emitter properties의 Rays 값을 사용하며 전체 ray 수는 emitter별 값의 합입니다.</div>
            </details>
          </div>
          </div>
          </div>

          <div class=\"side-tab-panel\" data-side-panel=\"result\">
          <button type=\"button\" class=\"accordion-btn\" data-side-tab=\"result\">Result</button>
          <div class=\"side-panel-body\">
          <div class=\"card\">
            <div class=\"step\">Step 5</div>
            <h2>Result</h2>
            <div id=\"rayDisplayPanel\" class=\"ray-filter-panel hidden-block\">
              <div class=\"ray-filter-panel-head\">
                <span>3D Ray path 표시</span>
                <span id=\"rayDisplayCount\" class=\"ray-filter-count\">0/0</span>
              </div>
              <label class=\"ray-filter-row receiver\">
                <input type=\"checkbox\" data-ray-filter=\"receiver_direct\" checked>
                <span class=\"ray-swatch\" style=\"--ray-color:#4ade80\"></span>
                <span>Receiver 도달 · Direct</span>
              </label>
              <label class=\"ray-filter-row receiver\">
                <input type=\"checkbox\" data-ray-filter=\"receiver_reflected\" checked>
                <span class=\"ray-swatch\" style=\"--ray-color:#facc15\"></span>
                <span>Receiver 도달 · 반사광</span>
              </label>
              <div class=\"ray-filter-divider\"></div>
              <label class=\"ray-filter-row\">
                <input type=\"checkbox\" data-ray-filter=\"direct\" checked>
                <span class=\"ray-swatch\" style=\"--ray-color:#60a5fa\"></span>
                <span>Direct / 최초 진행</span>
              </label>
              <label class=\"ray-filter-row\">
                <input type=\"checkbox\" data-ray-filter=\"specular\" checked>
                <span class=\"ray-swatch\" style=\"--ray-color:#fb923c\"></span>
                <span>Specular</span>
              </label>
              <label class=\"ray-filter-row\">
                <input type=\"checkbox\" data-ray-filter=\"lambertian\" checked>
                <span class=\"ray-swatch\" style=\"--ray-color:#c084fc\"></span>
                <span>Lambertian</span>
              </label>
              <label class=\"ray-filter-row\">
                <input type=\"checkbox\" data-ray-filter=\"gaussian\" checked>
                <span class=\"ray-swatch\" style=\"--ray-color:#22d3ee\"></span>
                <span>Gaussian</span>
              </label>
              <div class=\"ray-filter-actions\">
                <button id=\"rayReceiverOnlyBtn\" type=\"button\">Receiver only</button>
                <button id=\"rayAllOnBtn\" type=\"button\">All on</button>
                <button id=\"rayAllOffBtn\" type=\"button\">All off</button>
              </div>
              <div class=\"ray-filter-note\">기본값은 모든 저장 경로 표시입니다. 체크를 해제하면 해당 유형이 3D Viewer에서 즉시 숨겨집니다.</div>
            </div>
            <div id=\"resultPlaceholder\" class=\"manager-empty\">Ray tracing 완료 후 3D viewer 위에 분석 결과창이 표시됩니다.</div>
            <button id=\"openRayTraceResultBtn\" class=\"result-popup-launch\" type=\"button\" disabled>분석 결과 보기</button>
            <div id=\"resultPanel\" class=\"result-card\" style=\"display:none;\"></div>
          </div>
          </div>
          </div>
        </div>
      </form>
    </aside>

    <main class=\"viewer-wrap\">
      <div class=\"viewer-head\">
        <h3>3D viewer</h3>
        <div class=\"viewer-tools\">
          <div class=\"viewer-tool-group\">
            <div class=\"tool-title\">Camera</div>
            <div class=\"camera-buttons\" id=\"cameraPresetGroup\">
              <button type=\"button\" class=\"camera-btn\" data-camera=\"fit\">Fit</button>
              <button type=\"button\" class=\"camera-btn\" data-camera=\"iso\">Iso</button>
              <button type=\"button\" class=\"camera-btn\" data-camera=\"xy\">XY</button>
              <button type=\"button\" class=\"camera-btn\" data-camera=\"xy_rev\">-XY</button>
              <button type=\"button\" class=\"camera-btn\" data-camera=\"xz\">XZ</button>
              <button type=\"button\" class=\"camera-btn\" data-camera=\"xz_rev\">-XZ</button>
              <button type=\"button\" class=\"camera-btn\" data-camera=\"yz\">YZ</button>
              <button type=\"button\" class=\"camera-btn\" data-camera=\"yz_rev\">-YZ</button>
            </div>
          </div>
          <div class=\"viewer-tool-group hidden-block\" aria-hidden=\"true\">
            <div class=\"tool-title\">Viewer</div>
            <div class=\"mode-buttons\" id=\"viewerEngineGroup\">
              <button type=\"button\" class=\"mode-btn active\" data-viewer-engine=\"three\">Three.js</button>
            </div>
          </div>
          <div class=\"viewer-tool-group\">
            <div class=\"tool-title\">Render mode</div>
            <div class=\"mode-buttons\" id=\"renderModeGroup\">
              <button type=\"button\" class=\"mode-btn active\" data-render-mode=\"wireframe\">Wireframe</button>
              <button type=\"button\" class=\"mode-btn\" data-render-mode=\"surface\">Surface</button>
              <button type=\"button\" class=\"mode-btn\" data-render-mode=\"surface_edges\">Surface + Edge</button>
            </div>
          </div>
          <div class=\"viewer-tool-group\">
            <div class=\"tool-title\">Axis size</div>
            <div class=\"range-wrap\">
              <input id=\"axisScale\" type=\"range\" min=\"50\" max=\"150\" step=\"5\" value=\"100\" />
              <span id=\"axisScaleValue\" class=\"range-value\">100%</span>
            </div>
          </div>
          <span id=\"renderModeBadge\" class=\"mode-badge\">Wireframe</span>
        </div>
        <div id=\"viewerTip\" class=\"tip\">Drag = rotate, Middle drag = rotate, Wheel = zoom, Right click = component menu, Right drag = pan, Shift/Alt+drag = roll.</div>
      </div>
      <div class=\"viewer-inner\">
        <div class=\"kpi\">
          <div class=\"v\"><div class=\"label\">Face</div><div id=\"kpiFaces\" class=\"num\">0</div></div>
          <div class=\"v\"><div class=\"label\">Vertex</div><div id=\"kpiVerts\" class=\"num\">0</div></div>
          <div class=\"v\"><div class=\"label\">Mode</div><div id=\"kpiMode\" class=\"num\">-</div></div>
        </div>
        <div id=\"viewerStage\" class=\"viewer-stage mode-full\">
          <section class=\"viewer-card viewer-card-full\">
            <div class=\"viewer-card-head\">
              <strong>Full CAD View</strong>
              <span id=\"fullViewHint\" class=\"mini\">Imported model</span>
            </div>
            <canvas id=\"canvas3d\" class=\"viewer-canvas\"></canvas>
            <div id=\"threeFullViewer\" class=\"three-viewer\"></div>
          </section>
          <section class=\"viewer-card viewer-card-roi\">
            <div class=\"viewer-card-head\">
              <strong>ROI View</strong>
              <span id=\"roiViewHint\" class=\"mini\">ROI preview</span>
            </div>
            <canvas id=\"roiCanvas\" class=\"viewer-canvas\"></canvas>
            <div id=\"threeRoiViewer\" class=\"three-viewer\"></div>
          </section>
          <details class=\"coord-badge\">
            <summary>
              <span class=\"t\">World coordinates</span>
              <span id=\"coordSummary\" class=\"s\">No model loaded</span>
            </summary>
            <div id=\"coordReadout\" class=\"v\">Origin: (0, 0, 0)</div>
          </details>
        </div>
        <div id=\"viewerMovePanel\" class=\"move-panel viewer-move-panel-disabled hidden-block\" aria-hidden=\"true\">
          <div class=\"move-title\">
            <span>Transform preview</span>
            <span id=\"viewerMoveChip\" class=\"move-chip\">No object</span>
          </div>
          <div id=\"viewerMoveName\" class=\"move-sub\">3D viewer에서 부품을 클릭하면 선택됩니다.</div>
          <div class=\"move-sub\">실제 입력은 아래 Transform popup에서 수행합니다.</div>
          <div id=\"viewerMoveSummary\" class=\"move-summary\">선택된 객체 없음</div>
        </div>
        <div id=\"cursorMovePopup\" class=\"move-popup hidden-block\">
          <div id=\"cursorMovePopupHeader\" class=\"move-title\">
            <span>Transform input</span>
            <button id=\"cursorMoveClose\" type=\"button\" class=\"move-close\">Close</button>
          </div>
          <div id=\"cursorMoveName\" class=\"move-sub\">선택된 객체 없음</div>
          <div class=\"move-stack\">
            <label>Transform type
              <select id=\"gapTargetMode\">
                <option value=\"component_move_gap\" selected>부품 전체 이동 (기본)</option>
                <option value=\"face_gap\">선택 면만 이동</option>
              </select>
            </label>
            <label>Selection mode
              <select id=\"gapSelectionMethod\">
                <option value=\"click\" selected>클릭 선택</option>
                <option value=\"drag_box\">드래그 박스 선택</option>
              </select>
            </label>
          </div>
          <div class=\"move-grid\">
            <label>X (mm)<input id=\"cursorMoveX\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
            <label>Y (mm)<input id=\"cursorMoveY\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
            <label>Z (mm)<input id=\"cursorMoveZ\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
          </div>
          <div class=\"move-grid\" style=\"margin-top:8px;\">
            <label>Rx<input id=\"cursorTiltX\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
            <label>Ry<input id=\"cursorTiltY\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
            <label>Rz<input id=\"cursorTiltZ\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
          </div>
          <div class=\"move-actions\">
            <button id=\"cursorApplyBtn\" type=\"button\" class=\"primary\">Apply</button>
            <button id=\"cursorResetBtn\" type=\"button\">Reset</button>
            <button id=\"cursorRestoreBtn\" type=\"button\">Restore original</button>
          </div>
          <details class=\"popup-details\">
            <summary>Preview / applied details</summary>
            <div id=\"cursorMoveSummary\" class=\"move-summary\">선택된 객체 없음</div>
          </details>
        </div>
        <div id=\"cursorMaterialPopup\" class=\"move-popup material-popup hidden-block\">
          <div id=\"cursorMaterialPopupHeader\" class=\"move-title\">
            <span>Material assign</span>
            <button id=\"cursorMaterialClose\" type=\"button\" class=\"move-close\">Close</button>
          </div>
          <div class=\"material-popup-card\">
            <div class=\"material-popup-label\">Target</div>
            <div class=\"material-popup-target\">
              <span id=\"cursorMaterialChip\" class=\"move-chip\">No target</span>
              <button id=\"cursorMaterialOpenLibrary\" type=\"button\" class=\"move-close\">Library</button>
            </div>
            <div id=\"cursorMaterialName\" class=\"material-popup-target-name\">선택된 material 대상 없음</div>
            <div class=\"material-popup-note\">부품 지정은 여기서 하고, 라이브러리 편집은 왼쪽 탭에서 수행합니다.</div>
          </div>
          <div class=\"material-popup-card\">
            <div class=\"material-popup-label\">Assign</div>
            <div class=\"move-stack\">
              <label>Apply mode
                <select id=\"materialTargetMode\">
                  <option value=\"part\" selected>Part assignment</option>
                  <option value=\"faces\">Face override</option>
                </select>
              </label>
              <label>Base material
                <select id=\"materialPopupBaseSelect\"></select>
              </label>
              <label>Surface finish
                <select id=\"materialPopupSurfaceSelect\"></select>
              </label>
              <label>Saved optical profile
                <select id=\"materialPopupProfileSelect\"></select>
              </label>
            </div>
            <div class=\"material-actions\">
              <button id=\"materialApplyBtn\" type=\"button\" class=\"primary\">Apply</button>
              <button id=\"materialApplyFacesBtn\" type=\"button\">To faces</button>
              <button id=\"materialSaveProfileBtn\" type=\"button\">Save profile</button>
            </div>
          </div>
          <div id=\"cursorMaterialSummary\" class=\"move-summary\">선택된 material 대상 없음</div>
        </div>
        <div id=\"cursorEmitterPopup\" class=\"move-popup emitter-popup hidden-block\">
          <div id=\"cursorEmitterPopupHeader\" class=\"move-title\">
            <span>Emitter properties</span>
            <button id=\"cursorEmitterClose\" type=\"button\" class=\"move-close\">Close</button>
          </div>
          <div id=\"cursorEmitterNameHint\" class=\"move-sub\">3D viewer에서 방출 면을 선택하세요.</div>
          <div class=\"material-popup-target\">
            <span id=\"cursorEmitterChip\" class=\"move-chip\">New emitter</span>
            <button id=\"emitterReselectFacesBtn\" type=\"button\" class=\"move-close\">Select geometry</button>
          </div>
          <div class=\"move-stack\">
            <label>Name<input id=\"emitterNameInput\" type=\"text\" value=\"Emitter 1\"></label>
          </div>
          <div class=\"move-stack\">
            <label>Power mode
              <select id=\"emitterPowerModeSelect\">
                <option value=\"total\" selected>Total power</option>
                <option value=\"power_per_area\">Power per area</option>
              </select>
            </label>
          </div>
          <div class=\"move-grid\">
            <label id=\"emitterTotalPowerLabel\">Total power (lm)<input id=\"emitterPowerInput\" type=\"text\" inputmode=\"decimal\" value=\"1.0\"></label>
            <label id=\"emitterPowerDensityLabel\" class=\"hidden-block\">Power / area (lm/m²)<input id=\"emitterPowerDensityInput\" type=\"text\" inputmode=\"decimal\" value=\"100\"></label>
            <label>Rays<input id=\"emitterRayCountInput\" type=\"number\" min=\"1\" step=\"1000\" value=\"10000\"></label>
            <label>Targets<input id=\"emitterFaceCountInput\" type=\"text\" value=\"0\" readonly></label>
          </div>
          <div id=\"emitterDatumSection\" class=\"emitter-geometry-section hidden-block\">
            <div class=\"move-sub\">Datum plane geometry</div>
            <div class=\"move-grid\">
              <label>Center X<input id=\"emitterCenterX\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
              <label>Center Y<input id=\"emitterCenterY\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
              <label>Center Z<input id=\"emitterCenterZ\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
            </div>
            <div class=\"move-grid\">
              <label>Width (mm)<input id=\"emitterWidthInput\" type=\"text\" inputmode=\"decimal\" value=\"20\"></label>
              <label>Height (mm)<input id=\"emitterHeightInput\" type=\"text\" inputmode=\"decimal\" value=\"20\"></label>
              <label>Area (mm²)<input id=\"emitterAreaInput\" type=\"text\" value=\"400\" readonly></label>
            </div>
            <div class=\"move-grid\">
              <label>Rx (deg)<input id=\"emitterRotationX\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
              <label>Ry (deg)<input id=\"emitterRotationY\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
              <label>Rz (deg)<input id=\"emitterRotationZ\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
            </div>
          </div>
          <div id=\"emitterReferenceSection\" class=\"emitter-geometry-section hidden-block\">
            <div class=\"move-stack\">
              <label>Reference method
                <select id=\"emitterReferenceModeSelect\">
                  <option value=\"three_vertices\" selected>3–6 vertices</option>
                  <option value=\"two_edges\">2 edges</option>
                </select>
              </label>
              <label id=\"emitterReferenceSurfaceWrap\">Surface construction
                <select id=\"emitterReferenceSurfaceSelect\">
                  <option value=\"rectangular_fit\" selected>Plane containing vertices</option>
                  <option value=\"polygon_auto\">Polygon – Auto closed boundary</option>
                </select>
              </label>
            </div>
            <div id=\"emitterReferenceHint\" class=\"field-note\">3개 이상 선택하면 평면이 생성되며 최대 6개까지 선택할 수 있습니다.</div>
            <div class=\"reference-selection-tools\">
              <button id=\"emitterClearReferencesBtn\" type=\"button\" class=\"reference-clear-btn\">Clear selected points</button>
              <span id=\"emitterReferenceCount\" class=\"reference-count\">0 / 6 selected</span>
            </div>
          </div>
          <div class=\"move-stack\">
            <label>Direction distribution
              <select id=\"emitterDistributionSelect\">
                <option value=\"lambertian\" selected>Lambertian (default)</option>
                <option value=\"isotropic\">Isotropic</option>
                <option value=\"gaussian\">Gaussian</option>
              </select>
            </label>
            <label id=\"emitterGaussianSigmaLabel\" class=\"hidden-block\">Gaussian sigma (deg)
              <input id=\"emitterGaussianSigmaInput\" type=\"number\" min=\"0.1\" step=\"0.5\" value=\"12.0\">
            </label>
          </div>
          <label class=\"emitter-check\"><input id=\"emitterNormalFlipInput\" type=\"checkbox\"> Flip normal direction</label>
          <div class=\"move-actions\">
            <button id=\"emitterApplyBtn\" type=\"button\" class=\"primary\">Apply</button>
            <button id=\"emitterResetBtn\" type=\"button\" title=\"Power, size and direction fields only\">Reset properties</button>
            <button id=\"emitterDeleteBtn\" type=\"button\">Delete</button>
          </div>
          <details class=\"popup-details\">
            <summary>Geometry details</summary>
            <div id=\"emitterGeometrySummary\" class=\"move-summary\">선택 면 없음</div>
          </details>
        </div>
        <div id=\"cursorReceiverPopup\" class=\"move-popup receiver-popup hidden-block\">
          <div id=\"cursorReceiverPopupHeader\" class=\"move-title\">
            <span>Receiver properties</span>
            <button id=\"cursorReceiverClose\" type=\"button\" class=\"move-close\">Close</button>
          </div>
          <div id=\"cursorReceiverNameHint\" class=\"move-sub\">가상 측정면의 위치와 방향을 정의합니다.</div>
          <div class=\"material-popup-target\">
            <span id=\"cursorReceiverChip\" class=\"move-chip\">New receiver</span>
            <button id=\"receiverReselectGeometryBtn\" type=\"button\" class=\"move-close\">Select geometry</button>
          </div>
          <div class=\"move-stack\">
            <label>Name<input id=\"receiverNameInput\" type=\"text\" value=\"Receiver 1\"></label>
          </div>
          <div class=\"move-grid\">
            <label>Width (mm)<input id=\"receiverWidthInput\" type=\"text\" inputmode=\"decimal\" value=\"100\"></label>
            <label>Height (mm)<input id=\"receiverHeightInput\" type=\"text\" inputmode=\"decimal\" value=\"30\"></label>
            <label>Area (mm²)<input id=\"receiverAreaInput\" type=\"text\" value=\"3000\" readonly></label>
          </div>
          <div id=\"receiverDatumSection\" class=\"emitter-geometry-section hidden-block\">
            <div class=\"move-sub\">Datum plane geometry</div>
            <div class=\"move-grid\">
              <label>Center X<input id=\"receiverCenterX\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
              <label>Center Y<input id=\"receiverCenterY\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
              <label>Center Z<input id=\"receiverCenterZ\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
            </div>
            <div class=\"move-grid\">
              <label>Rx (deg)<input id=\"receiverRotationX\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
              <label>Ry (deg)<input id=\"receiverRotationY\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
              <label>Rz (deg)<input id=\"receiverRotationZ\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
            </div>
          </div>
          <div id=\"receiverReferenceSection\" class=\"emitter-geometry-section hidden-block\">
            <div class=\"move-stack\">
              <label>Reference method
                <select id=\"receiverReferenceModeSelect\">
                  <option value=\"three_vertices\" selected>3–6 vertices</option>
                  <option value=\"two_edges\">2 edges</option>
                </select>
              </label>
            </div>
            <div id=\"receiverReferenceHint\" class=\"field-note\">3개 이상 선택하면 평면이 생성되며 최대 6개까지 선택할 수 있습니다.</div>
            <div class=\"reference-selection-tools\">
              <button id=\"receiverClearReferencesBtn\" type=\"button\" class=\"reference-clear-btn\">Clear selected points</button>
              <span id=\"receiverReferenceCount\" class=\"reference-count\">0 / 6 selected</span>
            </div>
          </div>
          <div id=\"receiverCurrentViewSection\" class=\"emitter-geometry-section hidden-block\">
            <div class=\"move-grid\">
              <label>View distance (mm)<input id=\"receiverViewDistanceInput\" type=\"text\" inputmode=\"decimal\" value=\"100\"></label>
              <button id=\"receiverCaptureViewBtn\" type=\"button\">Update from current view</button>
            </div>
            <div class=\"field-note\">현재 Full CAD View 카메라 방향과 화면 수평 방향을 Receiver 축으로 저장합니다.</div>
          </div>
          <details id=\"receiverAdjustmentSection\" class=\"popup-details hidden-block\">
            <summary>Position / Tilt adjustment</summary>
            <div class=\"move-sub\">기준면 생성 후 월드 좌표계 기준으로 추가 이동·회전합니다.</div>
            <div class=\"move-grid\">
              <label>Offset X (mm)<input id=\"receiverOffsetX\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
              <label>Offset Y (mm)<input id=\"receiverOffsetY\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
              <label>Offset Z (mm)<input id=\"receiverOffsetZ\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
            </div>
            <div class=\"move-grid\">
              <label>Tilt X (deg)<input id=\"receiverTiltX\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
              <label>Tilt Y (deg)<input id=\"receiverTiltY\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
              <label>Tilt Z (deg)<input id=\"receiverTiltZ\" type=\"text\" inputmode=\"decimal\" value=\"0\"></label>
            </div>
          </details>
          <div class=\"move-grid\">
            <label>Resolution X<input id=\"receiverResolutionX\" type=\"number\" min=\"1\" step=\"1\" value=\"80\"></label>
            <label>Resolution Y<input id=\"receiverResolutionY\" type=\"number\" min=\"1\" step=\"1\" value=\"24\"></label>
            <label>Acceptance (deg)<input id=\"receiverAcceptanceInput\" type=\"number\" min=\"0.1\" max=\"180\" step=\"1\" value=\"90\"></label>
          </div>
          <label class=\"emitter-check\"><input id=\"receiverNormalFlipInput\" type=\"checkbox\"> Flip receiving normal</label>
          <div class=\"move-actions\">
            <button id=\"receiverApplyBtn\" type=\"button\" class=\"primary\">Apply</button>
            <button id=\"receiverResetBtn\" type=\"button\" title=\"Size, resolution and acceptance fields only\">Reset properties</button>
            <button id=\"receiverDeleteBtn\" type=\"button\">Delete</button>
          </div>
          <details class=\"popup-details\">
            <summary>Geometry details</summary>
            <div id=\"receiverGeometrySummary\" class=\"move-summary\">Receiver geometry not set</div>
          </details>
        </div>
        <div id=\"rayTraceResultPopup\" class=\"move-popup ray-result-popup hidden-block\" role=\"dialog\" aria-modal=\"false\" aria-labelledby=\"rayTraceResultPopupTitle\">
          <div id=\"rayTraceResultPopupHeader\" class=\"move-title\">
            <div class=\"ray-result-popup-title\">
              <span id=\"rayTraceResultPopupTitle\">Ray Tracing Analysis Result</span>
              <span id=\"rayTraceResultPopupStatus\" class=\"ray-result-popup-status\">Complete</span>
            </div>
            <button id=\"rayTraceResultPopupClose\" type=\"button\" class=\"move-close\">Close</button>
          </div>
          <div id=\"rayTraceResultTabs\" class=\"ray-result-tabs\" role=\"tablist\" aria-label=\"Ray tracing result categories\">
            <button id=\"rayResultTabSummary\" class=\"ray-result-tab active\" type=\"button\" role=\"tab\" data-result-tab=\"ray_summary\" aria-controls=\"rayResultPanelSummary\" aria-selected=\"true\">Ray summary</button>
            <button id=\"rayResultTabOptical\" class=\"ray-result-tab\" type=\"button\" role=\"tab\" data-result-tab=\"surface_optical\" aria-controls=\"rayResultPanelOptical\" aria-selected=\"false\">Surface optical</button>
            <button id=\"rayResultTabBounce\" class=\"ray-result-tab\" type=\"button\" role=\"tab\" data-result-tab=\"multi_bounce\" aria-controls=\"rayResultPanelBounce\" aria-selected=\"false\">Multi-bounce</button>
            <button id=\"rayResultTabReceiver\" class=\"ray-result-tab\" type=\"button\" role=\"tab\" data-result-tab=\"receiver\" aria-controls=\"rayResultPanelReceiver\" aria-selected=\"false\">Receiver</button>
          </div>
          <div id=\"rayTraceResultPopupBody\" class=\"ray-result-popup-body\"></div>
          <div id=\"rayTraceResultResizeHandle\" class=\"ray-result-resize-handle\" role=\"separator\" aria-label=\"Resize result panel\" title=\"Drag to resize result panel\"></div>
        </div>
      </div>
    </main>
  </div>

  <div id=\"componentContextMenu\" class=\"component-context-menu hidden-block\" role=\"menu\" aria-hidden=\"true\" aria-label=\"Component actions\">
    <div class=\"component-context-menu-head\">
      <div id=\"componentContextMenuName\" class=\"component-context-menu-name\">Component</div>
      <div id=\"componentContextMenuStatus\" class=\"component-context-menu-status\">Visible · Traceability on</div>
    </div>
    <div class=\"component-context-menu-items\">
      <button id=\"componentContextVisibility\" type=\"button\" role=\"menuitem\" data-component-context-action=\"visibility\">Hide</button>
      <button id=\"componentContextTraceability\" type=\"button\" role=\"menuitem\" data-component-context-action=\"traceability\">Traceability Off</button>
      <div id=\"componentContextPrimarySeparator\" class=\"component-context-menu-separator\"></div>
      <button id=\"componentContextMaterial\" type=\"button\" role=\"menuitem\" data-component-context-action=\"material\">Material</button>
      <button id=\"componentContextTransform\" type=\"button\" role=\"menuitem\" data-component-context-action=\"transform\">Transform</button>
      <div id=\"componentContextDeleteSeparator\" class=\"component-context-menu-separator\"></div>
      <button id=\"componentContextDelete\" type=\"button\" class=\"danger\" role=\"menuitem\" data-component-context-action=\"delete\">Delete…</button>
    </div>
  </div>

  <script type=\"importmap\">
    {{
      \"imports\": {{
        \"three\": \"/static/vendor/three.module.min.js\"
      }}
    }}
  </script>
  <script type=\"module\">
    import * as THREE from 'three';
    import {{ OrbitControls }} from '/static/vendor/OrbitControls.js';

    function toVector3Array(value, fallback) {{
      const source = value || fallback || [0, 0, 0];
      if (Array.isArray(source)) return [Number(source[0]) || 0, Number(source[1]) || 0, Number(source[2]) || 0];
      return [Number(source.x) || 0, Number(source.y) || 0, Number(source.z) || 0];
    }}

    function rotatePointForThree(point, pivot, rotationDeg) {{
      let x = point[0] - pivot[0];
      let y = point[1] - pivot[1];
      let z = point[2] - pivot[2];
      const rx = (Number(rotationDeg?.x) || 0) * Math.PI / 180.0;
      const ry = (Number(rotationDeg?.y) || 0) * Math.PI / 180.0;
      const rz = (Number(rotationDeg?.z) || 0) * Math.PI / 180.0;
      if (Math.abs(rx) > 1e-12) {{
        const cosX = Math.cos(rx);
        const sinX = Math.sin(rx);
        const nextY = y * cosX - z * sinX;
        const nextZ = y * sinX + z * cosX;
        y = nextY;
        z = nextZ;
      }}
      if (Math.abs(ry) > 1e-12) {{
        const cosY = Math.cos(ry);
        const sinY = Math.sin(ry);
        const nextX = x * cosY + z * sinY;
        const nextZ = -x * sinY + z * cosY;
        x = nextX;
        z = nextZ;
      }}
      if (Math.abs(rz) > 1e-12) {{
        const cosZ = Math.cos(rz);
        const sinZ = Math.sin(rz);
        const nextX = x * cosZ - y * sinZ;
        const nextY = x * sinZ + y * cosZ;
        x = nextX;
        y = nextY;
      }}
      return [x + pivot[0], y + pivot[1], z + pivot[2]];
    }}

    function transformPointForThree(point, transformSpec) {{
      if (!transformSpec) return point;
      const pivot = toVector3Array(transformSpec.pivot, [0, 0, 0]);
      const move = transformSpec.move || {{ x: 0, y: 0, z: 0 }};
      const tilt = transformSpec.tilt || {{ x: 0, y: 0, z: 0 }};
      const rotated = rotatePointForThree(point, pivot, tilt);
      return [
        rotated[0] + (Number(move.x) || 0),
        rotated[1] + (Number(move.y) || 0),
        rotated[2] + (Number(move.z) || 0)
      ];
    }}

    function flattenFaces(faces, faceFilter, excludeFaces) {{
      const indices = [];
      const sourceFaceIds = [];
      const allowed = faceFilter ? new Set(faceFilter) : null;
      const excluded = excludeFaces ? new Set(excludeFaces) : null;
      for (let faceId = 0; faceId < faces.length; faceId++) {{
        if (allowed && !allowed.has(faceId)) continue;
        if (excluded && excluded.has(faceId)) continue;
        const tri = faces[faceId];
        indices.push(tri[0], tri[1], tri[2]);
        sourceFaceIds.push(faceId);
      }}
      return {{ indices, sourceFaceIds }};
    }}

    function buildBufferGeometry(mesh, faceFilter, options) {{
      const geometryOptions = options || {{}};
      const positions = new Float32Array(mesh.vertices.length * 3);
      for (let i = 0; i < mesh.vertices.length; i++) {{
        const v = mesh.vertices[i];
        const next = geometryOptions.transformSpec ? transformPointForThree(v, geometryOptions.transformSpec) : v;
        positions[i * 3] = next[0];
        positions[i * 3 + 1] = next[1];
        positions[i * 3 + 2] = next[2];
      }}
      const flat = flattenFaces(mesh.faces, faceFilter, geometryOptions.excludeFaces);
      const IndexArray = mesh.vertices.length > 65535 ? Uint32Array : Uint16Array;
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      geometry.setIndex(new THREE.BufferAttribute(new IndexArray(flat.indices), 1));
      geometry.computeVertexNormals();
      geometry.computeBoundingBox();
      geometry.computeBoundingSphere();
      geometry.userData.sourceFaceIds = flat.sourceFaceIds;
      return geometry;
    }}

    function buildVirtualPlaneGeometry(plane) {{
      const polygonPoints = Array.isArray(plane.polygonPoints) ? plane.polygonPoints : [];
      if (polygonPoints.length >= 3) {{
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(
          polygonPoints.flatMap((point) => toVector3Array(point, [0, 0, 0])),
          3
        ));
        const indices = [];
        for (let index = 1; index < polygonPoints.length - 1; index += 1) {{
          indices.push(0, index, index + 1);
        }}
        geometry.setIndex(indices);
        geometry.computeVertexNormals();
        geometry.computeBoundingBox();
        geometry.computeBoundingSphere();
        return geometry;
      }}
      const center = new THREE.Vector3(...toVector3Array(plane.center, [0, 0, 0]));
      const uAxis = new THREE.Vector3(...toVector3Array(plane.uAxis, [1, 0, 0])).normalize();
      const rawV = new THREE.Vector3(...toVector3Array(plane.vAxis, [0, 1, 0]));
      const vAxis = rawV.addScaledVector(uAxis, -rawV.dot(uAxis)).normalize();
      const halfWidth = Math.max(Number(plane.widthMm) || 1, 0.001) * 0.5;
      const halfHeight = Math.max(Number(plane.heightMm) || 1, 0.001) * 0.5;
      const corners = [
        center.clone().addScaledVector(uAxis, -halfWidth).addScaledVector(vAxis, -halfHeight),
        center.clone().addScaledVector(uAxis, halfWidth).addScaledVector(vAxis, -halfHeight),
        center.clone().addScaledVector(uAxis, halfWidth).addScaledVector(vAxis, halfHeight),
        center.clone().addScaledVector(uAxis, -halfWidth).addScaledVector(vAxis, halfHeight),
      ];
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(corners.flatMap((point) => point.toArray()), 3));
      geometry.setIndex([0, 1, 2, 0, 2, 3]);
      geometry.computeVertexNormals();
      geometry.computeBoundingBox();
      geometry.computeBoundingSphere();
      return geometry;
    }}

    function bboxCenterAndSize(mesh) {{
      if (!mesh || !mesh.vertices || !mesh.vertices.length) {{
        return {{ center: new THREE.Vector3(0, 0, 0), size: 1 }};
      }}
      const box = new THREE.Box3();
      for (const v of mesh.vertices) {{
        box.expandByPoint(new THREE.Vector3(v[0], v[1], v[2]));
      }}
      const center = new THREE.Vector3();
      const sizeVec = new THREE.Vector3();
      box.getCenter(center);
      box.getSize(sizeVec);
      return {{ center, size: Math.max(sizeVec.x, sizeVec.y, sizeVec.z, 1) }};
    }}

    function makeAxisLabel(text, color) {{
      const canvas = document.createElement('canvas');
      canvas.width = 96;
      canvas.height = 96;
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.font = '800 52px Segoe UI, Arial, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.lineWidth = 8;
      ctx.strokeStyle = 'rgba(2, 6, 23, 0.95)';
      ctx.strokeText(text, 48, 47);
      ctx.fillStyle = color;
      ctx.fillText(text, 48, 47);
      const texture = new THREE.CanvasTexture(canvas);
      texture.needsUpdate = true;
      const material = new THREE.SpriteMaterial({{ map: texture, transparent: true, depthTest: false }});
      const sprite = new THREE.Sprite(material);
      sprite.userData.axisLabel = true;
      return sprite;
    }}

    function makeAxisLine(points, color) {{
      const start = points[0];
      const end = points[1];
      const direction = end.clone().sub(start);
      const length = Math.max(direction.length(), 0.001);
      direction.normalize();
      const geometry = new THREE.CylinderGeometry(0.018, 0.018, length, 18);
      const material = new THREE.MeshBasicMaterial({{ color, depthTest: false }});
      const shaft = new THREE.Mesh(geometry, material);
      shaft.position.copy(start).add(end).multiplyScalar(0.5);
      shaft.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction);
      shaft.renderOrder = 60;
      return shaft;
    }}

    function makeAxisHead(color) {{
      const geometry = new THREE.ConeGeometry(0.045, 0.16, 20);
      const material = new THREE.MeshBasicMaterial({{ color, depthTest: false }});
      const head = new THREE.Mesh(geometry, material);
      head.renderOrder = 61;
      return head;
    }}

    function buildAxisTriad() {{
      const group = new THREE.Group();
      group.renderOrder = 50;
      const axes = [
        {{ name: 'X', color: '#ef4444', hex: 0xef4444, dir: new THREE.Vector3(1, 0, 0), quat: new THREE.Quaternion().setFromEuler(new THREE.Euler(0, 0, -Math.PI / 2)) }},
        {{ name: 'Y', color: '#22c55e', hex: 0x22c55e, dir: new THREE.Vector3(0, 1, 0), quat: new THREE.Quaternion().setFromEuler(new THREE.Euler(0, 0, 0)) }},
        {{ name: 'Z', color: '#3b82f6', hex: 0x3b82f6, dir: new THREE.Vector3(0, 0, 1), quat: new THREE.Quaternion().setFromEuler(new THREE.Euler(Math.PI / 2, 0, 0)) }},
      ];
      for (const axis of axes) {{
        const end = axis.dir.clone();
        const line = makeAxisLine([new THREE.Vector3(0, 0, 0), end], axis.hex);
        line.name = 'axis_' + axis.name + '_line';
        group.add(line);
        const head = makeAxisHead(axis.hex);
        head.name = 'axis_' + axis.name + '_head';
        head.position.copy(end);
        head.quaternion.copy(axis.quat);
        group.add(head);
        const label = makeAxisLabel(axis.name, axis.color);
        label.name = 'axis_' + axis.name + '_label';
        label.position.copy(axis.dir.clone().multiplyScalar(1.22));
        group.add(label);
      }}
      return group;
    }}

    class LeakageThreeViewer {{
      constructor(container, mode) {{
        this.container = container;
        this.mode = mode;
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0x020617);
        this.camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100000);
        this.renderer = new THREE.WebGLRenderer({{ antialias: true, alpha: false }});
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        this.renderer.setSize(1, 1);
        this.container.appendChild(this.renderer.domElement);

        this.controls = new OrbitControls(this.camera, this.renderer.domElement);
        this.controls.enabled = true;
        this.controls.enableRotate = true;
        this.controls.enableZoom = true;
        this.controls.enablePan = true;
        this.controls.enableDamping = true;
        this.controls.dampingFactor = 0.08;
        this.controls.rotateSpeed = 0.72;
        this.controls.zoomSpeed = 0.95;
        this.controls.panSpeed = 0.85;
        this.controls.mouseButtons = {{
          LEFT: null,
          MIDDLE: THREE.MOUSE.ROTATE,
          RIGHT: THREE.MOUSE.PAN
        }};
        this.controls.touches = {{
          ONE: THREE.TOUCH.ROTATE,
          TWO: THREE.TOUCH.DOLLY_PAN
        }};

        this.root = new THREE.Group();
        this.scene.add(this.root);
        this.overlayRoot = new THREE.Group();
        this.scene.add(this.overlayRoot);
        this.axis = buildAxisTriad();
        this.scene.add(this.axis);
        this.scene.add(new THREE.HemisphereLight(0xffffff, 0x334155, 2.2));
        const light = new THREE.DirectionalLight(0xffffff, 2.4);
        light.position.set(1, -2, 3);
        this.scene.add(light);

        this.mesh = null;
        this.raycaster = new THREE.Raycaster();
        this.pointer = new THREE.Vector2();
        this.pointerDown = null;
        this.freeRotateDrag = {{ active: false, lastX: 0, lastY: 0 }};
        this.rollDrag = {{ active: false, lastX: 0 }};
        this.center = new THREE.Vector3(0, 0, 0);
        this.size = 1;
        this.renderMode = 'wireframe';
        this.axisScalePercent = 100;
        this.lastMeshRef = null;
        this.resizeObserver = new ResizeObserver(() => this.resize());
        this.resizeObserver.observe(this.container);
        this.renderer.domElement.addEventListener('contextmenu', (ev) => this.handleContextMenu(ev));
        this.renderer.domElement.addEventListener('pointerdown', (ev) => this.handlePointerDown(ev));
        this.renderer.domElement.addEventListener('pointermove', (ev) => this.handlePointerMove(ev));
        this.renderer.domElement.addEventListener('pointerup', (ev) => this.handlePointerUp(ev));
        this.renderer.domElement.addEventListener('pointercancel', (ev) => this.handlePointerCancel(ev));
        this.animate = this.animate.bind(this);
        requestAnimationFrame(this.animate);
      }}

      handleContextMenu(ev) {{
        ev.preventDefault();
        ev.stopPropagation();
      }}

      handlePointerDown(ev) {{
        this.pointerDown = {{
          x: ev.clientX,
          y: ev.clientY,
          button: ev.button,
          ctrlKey: ev.ctrlKey,
          metaKey: ev.metaKey,
          shiftKey: ev.shiftKey,
          altKey: ev.altKey,
          totalMove: 0,
          lastX: ev.clientX,
          lastY: ev.clientY
        }};
        if ((ev.shiftKey || ev.altKey) && ev.button === 0) {{
          this.rollDrag.active = true;
          this.rollDrag.lastX = ev.clientX;
          this.controls.enabled = false;
          this.renderer.domElement.setPointerCapture?.(ev.pointerId);
          ev.preventDefault();
        }} else if (ev.button === 0) {{
          this.freeRotateDrag.active = true;
          this.freeRotateDrag.lastX = ev.clientX;
          this.freeRotateDrag.lastY = ev.clientY;
          this.controls.enabled = false;
          this.renderer.domElement.setPointerCapture?.(ev.pointerId);
          ev.preventDefault();
        }}
      }}

      handlePointerMove(ev) {{
        if (this.pointerDown) {{
          this.pointerDown.totalMove += Math.abs(ev.clientX - this.pointerDown.lastX)
            + Math.abs(ev.clientY - this.pointerDown.lastY);
          this.pointerDown.lastX = ev.clientX;
          this.pointerDown.lastY = ev.clientY;
        }}
        if (this.rollDrag.active) {{
          const dx = ev.clientX - this.rollDrag.lastX;
          this.rollDrag.lastX = ev.clientX;
          this.rollCamera(dx * 0.012);
          ev.preventDefault();
          return;
        }}
        if (this.freeRotateDrag.active) {{
          const dx = ev.clientX - this.freeRotateDrag.lastX;
          const dy = ev.clientY - this.freeRotateDrag.lastY;
          this.freeRotateDrag.lastX = ev.clientX;
          this.freeRotateDrag.lastY = ev.clientY;
          this.freeRotateCamera(dx, dy);
          ev.preventDefault();
        }}
      }}

      handlePointerUp(ev) {{
        const wasRoll = this.rollDrag.active;
        this.handlePointerCancel(ev);
        if (wasRoll || !this.pointerDown) return;
        const move = Math.max(
          this.pointerDown.totalMove || 0,
          Math.abs(ev.clientX - this.pointerDown.x) + Math.abs(ev.clientY - this.pointerDown.y)
        );
        const isPrimaryClick = this.pointerDown.button === 0 && ev.button === 0;
        const isSecondaryClick = this.pointerDown.button === 2 && ev.button === 2;
        if (isPrimaryClick && move <= 6) {{
          const pick = this.pickGeometry(ev);
          this.container.dispatchEvent(new CustomEvent('leakage-three-pick', {{
            bubbles: true,
            detail: {{
              faceIndex: pick ? pick.faceIndex : null,
              point: pick ? pick.point : null,
              vertexIndex: pick ? pick.vertexIndex : null,
              edgeVertexIndices: pick ? pick.edgeVertexIndices : null,
              mode: this.mode,
              clientX: ev.clientX,
              clientY: ev.clientY,
              ctrlKey: ev.ctrlKey || this.pointerDown.ctrlKey,
              metaKey: ev.metaKey || this.pointerDown.metaKey,
              shiftKey: ev.shiftKey || this.pointerDown.shiftKey,
              altKey: ev.altKey || this.pointerDown.altKey
            }}
          }}));
        }} else if (isSecondaryClick && move <= 6) {{
          const pick = this.pickGeometry(ev, true);
          this.container.dispatchEvent(new CustomEvent('leakage-three-contextmenu', {{
            bubbles: true,
            detail: {{
              faceIndex: pick ? pick.faceIndex : null,
              mode: this.mode,
              clientX: ev.clientX,
              clientY: ev.clientY
            }}
          }}));
        }}
        this.pointerDown = null;
      }}

      handlePointerCancel(ev) {{
        if (this.rollDrag.active || this.freeRotateDrag.active) {{
          this.rollDrag.active = false;
          this.freeRotateDrag.active = false;
          this.controls.enabled = true;
          try {{
            this.renderer.domElement.releasePointerCapture?.(ev.pointerId);
          }} catch (err) {{}}
        }}
      }}

      rollCamera(angleRad) {{
        const viewAxis = new THREE.Vector3().subVectors(this.camera.position, this.controls.target).normalize();
        this.camera.up.applyAxisAngle(viewAxis, -angleRad).normalize();
        this.camera.lookAt(this.controls.target);
        this.controls.update();
      }}

      freeRotateCamera(dx, dy) {{
        if (!dx && !dy) return;
        const target = this.controls.target;
        const offset = new THREE.Vector3().subVectors(this.camera.position, target);
        const yawQuat = new THREE.Quaternion().setFromAxisAngle(this.camera.up.clone().normalize(), -dx * 0.008);
        offset.applyQuaternion(yawQuat);
        this.camera.updateMatrixWorld();
        const rightAxis = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 0).normalize();
        if (rightAxis.lengthSq() > 1e-10) {{
          const pitchQuat = new THREE.Quaternion().setFromAxisAngle(rightAxis, -dy * 0.008);
          offset.applyQuaternion(pitchQuat);
          this.camera.up.applyQuaternion(pitchQuat).normalize();
        }}
        this.camera.position.copy(target).add(offset);
        this.camera.lookAt(target);
        this.controls.update();
      }}

      pickGeometry(ev, componentOnly = false) {{
        const rect = this.renderer.domElement.getBoundingClientRect();
        this.pointer.x = ((ev.clientX - rect.left) / Math.max(rect.width, 1)) * 2 - 1;
        this.pointer.y = -((ev.clientY - rect.top) / Math.max(rect.height, 1)) * 2 + 1;
        this.raycaster.setFromCamera(this.pointer, this.camera);
        const candidates = [];
        const surface = this.root.getObjectByName('surface');
        if (surface) candidates.push(surface);
        for (const child of this.overlayRoot.children) {{
          if (!child.isMesh) continue;
          const overlayKind = child.userData ? String(child.userData.overlayKind || '') : '';
          const isPlacementOverlay = overlayKind.startsWith('emitter_') || overlayKind.startsWith('receiver_');
          if (componentOnly && isPlacementOverlay) continue;
          if (!componentOnly && this.pickBaseOnly && isPlacementOverlay) continue;
          candidates.push(child);
        }}
        const hits = this.raycaster.intersectObjects(candidates, false);
        if (!hits.length) return null;
        for (const hit of hits) {{
          const sourceFaceIds = hit.object.geometry && hit.object.geometry.userData
            ? hit.object.geometry.userData.sourceFaceIds
            : null;
          if (!sourceFaceIds || hit.faceIndex === null || hit.faceIndex === undefined) continue;
          const sourceFace = sourceFaceIds[hit.faceIndex];
          if (sourceFace === null || sourceFace === undefined) continue;
          const meshRef = this.lastMeshRef;
          const triangle = meshRef && meshRef.faces ? meshRef.faces[sourceFace] : null;
          if (!triangle) return {{ faceIndex: sourceFace, point: hit.point.toArray(), vertexIndex: null, edgeVertexIndices: null }};
          let nearestVertex = triangle[0];
          let nearestVertexDistance = Infinity;
          for (const vertexIndex of triangle) {{
            const vertex = meshRef.vertices[vertexIndex];
            const distance = hit.point.distanceTo(new THREE.Vector3(vertex[0], vertex[1], vertex[2]));
            if (distance < nearestVertexDistance) {{
              nearestVertexDistance = distance;
              nearestVertex = vertexIndex;
            }}
          }}
          const edges = [[triangle[0], triangle[1]], [triangle[1], triangle[2]], [triangle[2], triangle[0]]];
          let nearestEdge = edges[0];
          let nearestEdgeDistance = Infinity;
          for (const edge of edges) {{
            const startValue = meshRef.vertices[edge[0]];
            const endValue = meshRef.vertices[edge[1]];
            const start = new THREE.Vector3(startValue[0], startValue[1], startValue[2]);
            const end = new THREE.Vector3(endValue[0], endValue[1], endValue[2]);
            const segment = end.clone().sub(start);
            const lengthSq = Math.max(segment.lengthSq(), 1e-12);
            const factor = Math.max(0, Math.min(1, hit.point.clone().sub(start).dot(segment) / lengthSq));
            const closest = start.clone().add(segment.multiplyScalar(factor));
            const distance = closest.distanceTo(hit.point);
            if (distance < nearestEdgeDistance) {{
              nearestEdgeDistance = distance;
              nearestEdge = edge;
            }}
          }}
          return {{
            faceIndex: sourceFace,
            point: hit.point.toArray(),
            vertexIndex: nearestVertex,
            edgeVertexIndices: nearestEdge
          }};
        }}
        return null;
      }}

      clearRoot() {{
        this.clearGroup(this.root);
      }}

      clearOverlays() {{
        this.clearGroup(this.overlayRoot);
      }}

      clearGroup(group) {{
        while (group.children.length) {{
          const child = group.children.pop();
          child.traverse((node) => {{
            if (node.geometry) node.geometry.dispose();
            if (node.material) {{
              if (Array.isArray(node.material)) node.material.forEach((mat) => mat.dispose());
              else node.material.dispose();
            }}
          }});
        }}
      }}

      visibleOverlayFaces(overlay, selectedFaces) {{
        const sourceFaces = overlay?.faceIndices || [];
        if (this.mode !== 'roi' || !selectedFaces || !selectedFaces.length) return sourceFaces;
        const allowed = new Set(selectedFaces);
        return sourceFaces.filter((faceId) => allowed.has(faceId));
      }}

      updateOverlays(meshRef, options) {{
        this.clearOverlays();
        const overlays = options?.overlays || [];
        const selectedFaces = options?.selectedFaces || [];
        for (const overlay of overlays) {{
          const faceIndices = this.visibleOverlayFaces(overlay, selectedFaces);
          const geometry = overlay.virtualPlane
            ? buildVirtualPlaneGeometry(overlay.virtualPlane)
            : (faceIndices.length ? buildBufferGeometry(meshRef, faceIndices, {{ transformSpec: overlay }}) : null);
          if (geometry) {{
            const surface = new THREE.Mesh(
              geometry,
              new THREE.MeshStandardMaterial({{
                color: overlay.color || 0xef4444,
                roughness: 0.72,
                metalness: 0.02,
                transparent: true,
                opacity: overlay.opacity ?? 0.48,
                side: THREE.DoubleSide,
                depthTest: true,
                depthWrite: false,
              }})
            );
            surface.name = 'transform_' + (overlay.kind || 'overlay');
            surface.userData.overlayKind = overlay.kind || 'overlay';
            this.overlayRoot.add(surface);

            const edges = new THREE.LineSegments(
              new THREE.EdgesGeometry(geometry, 18),
              new THREE.LineBasicMaterial({{
                color: overlay.edgeColor || overlay.color || 0xf87171,
                transparent: true,
                opacity: overlay.edgeOpacity ?? 0.95,
                depthTest: false,
              }})
            );
            edges.name = 'transform_edges_' + (overlay.kind || 'overlay');
            this.overlayRoot.add(edges);
          }}
          if (overlay.referencePoints && overlay.referencePoints.length) {{
            for (const pointValue of overlay.referencePoints) {{
              const marker = new THREE.Mesh(
                new THREE.SphereGeometry(Math.max(this.size * 0.008, 0.25), 16, 12),
                new THREE.MeshBasicMaterial({{ color: 0x22d3ee, depthTest: false }})
              );
              marker.position.set(...toVector3Array(pointValue, [0, 0, 0]));
              marker.renderOrder = 85;
              this.overlayRoot.add(marker);
            }}
          }}
          if (overlay.referenceSegments && overlay.referenceSegments.length) {{
            for (const segment of overlay.referenceSegments) {{
              const segmentGeometry = new THREE.BufferGeometry().setFromPoints([
                new THREE.Vector3(...toVector3Array(segment[0], [0, 0, 0])),
                new THREE.Vector3(...toVector3Array(segment[1], [0, 0, 0]))
              ]);
              const segmentLine = new THREE.Line(
                segmentGeometry,
                new THREE.LineBasicMaterial({{
                  color: overlay.segmentColor || 0x22d3ee,
                  transparent: true,
                  opacity: overlay.segmentOpacity ?? 0.92,
                  depthTest: false,
                }})
              );
              segmentLine.name = 'ray_segment_' + (overlay.kind || 'overlay');
              segmentLine.userData.overlayKind = overlay.kind || 'overlay';
              segmentLine.renderOrder = 84;
              this.overlayRoot.add(segmentLine);
            }}
          }}
          if (overlay.arrowOrigin && overlay.arrowDirection) {{
            const origin = new THREE.Vector3(...toVector3Array(overlay.arrowOrigin, [0, 0, 0]));
            const direction = new THREE.Vector3(...toVector3Array(overlay.arrowDirection, [0, 0, 1])).normalize();
            const length = Math.max(Number(overlay.arrowLength) || 1, 0.001);
            const arrow = new THREE.ArrowHelper(
              direction,
              origin,
              length,
              overlay.arrowColor || 0xfbbf24,
              Math.max(length * 0.22, 0.2),
              Math.max(length * 0.10, 0.08)
            );
            arrow.name = 'emitter_direction_' + (overlay.kind || 'overlay');
            arrow.renderOrder = 80;
            arrow.traverse((node) => {{
              if (node.material) {{
                node.material.depthTest = false;
                node.material.transparent = true;
                node.material.opacity = 0.98;
              }}
            }});
            this.overlayRoot.add(arrow);
          }}
        }}
      }}

      setScene(payload, options) {{
        if (!payload || !payload.mesh) return;
        const selectedFaces = options?.selectedFaces || [];
        const hiddenFaces = options?.hiddenFaces || [];
        const roiFaces = this.mode === 'roi' && selectedFaces.length ? selectedFaces : null;
        const meshRef = payload.mesh;
        const roiKey = JSON.stringify(roiFaces || []);
        const hiddenKey = JSON.stringify(hiddenFaces || []);
        const meshChanged = this.lastMeshRef !== meshRef;
        const roiChanged = this.lastRoiKey !== roiKey;
        const hiddenChanged = this.lastHiddenKey !== hiddenKey;
        const needsRebuild = meshChanged || roiChanged || hiddenChanged;
        this.renderMode = options?.renderMode || 'wireframe';
        this.pickBaseOnly = !!options?.pickBaseOnly;
        if (needsRebuild) {{
          this.clearRoot();
          const geometry = buildBufferGeometry(meshRef, roiFaces, {{ excludeFaces: hiddenFaces }});
          const surface = new THREE.Mesh(
            geometry,
            new THREE.MeshStandardMaterial({{
              color: 0x8fb3c7,
              roughness: 0.72,
              metalness: 0.04,
              transparent: true,
              opacity: 0.72,
              side: THREE.DoubleSide,
            }})
          );
          surface.name = 'surface';
          this.root.add(surface);

          const edgeGeometry = new THREE.EdgesGeometry(geometry, 18);
          const edges = new THREE.LineSegments(
            edgeGeometry,
            new THREE.LineBasicMaterial({{ color: 0xdbeafe, transparent: true, opacity: 0.46 }})
          );
          edges.name = 'edges';
          this.root.add(edges);

          this.lastMeshRef = meshRef;
          this.lastRoiKey = roiKey;
          this.lastHiddenKey = hiddenKey;
          const bounds = bboxCenterAndSize(meshRef);
          this.center = bounds.center;
          this.size = bounds.size;
          this.updateAxisScale(options?.axisScalePercent);
          if (meshChanged || roiChanged) {{
            this.fit();
          }}
        }}
        this.updateAxisScale(options?.axisScalePercent);
        this.applyRenderMode();
        this.updateOverlays(meshRef, options || {{}});
        this.resize();
      }}

      updateAxisScale(axisScalePercent) {{
        this.axisScalePercent = Number(axisScalePercent) || this.axisScalePercent || 100;
        const manualScale = Math.max(0.45, Math.min(1.75, this.axisScalePercent / 100.0));
        const axisSize = Math.max(this.size * 0.18 * manualScale, 1.0);
        this.axis.scale.setScalar(axisSize);
        this.axis.position.copy(this.center);
        for (const child of this.axis.children) {{
          if (child.userData && child.userData.axisLabel) {{
            child.scale.setScalar(Math.max(0.22, 0.18 * manualScale));
          }}
        }}
      }}

      applyRenderMode() {{
        const surface = this.root.getObjectByName('surface');
        const edges = this.root.getObjectByName('edges');
        if (!surface || !edges) return;
        if (this.renderMode === 'wireframe') {{
          surface.material.opacity = 0.10;
          surface.material.transparent = true;
          edges.visible = true;
          edges.material.opacity = 0.72;
        }} else if (this.renderMode === 'surface') {{
          surface.material.opacity = 0.66;
          surface.material.transparent = true;
          edges.visible = false;
        }} else {{
          surface.material.opacity = 0.88;
          surface.material.transparent = false;
          edges.visible = true;
          edges.material.opacity = 0.62;
        }}
        surface.material.needsUpdate = true;
        edges.material.needsUpdate = true;
      }}

      fit() {{
        const distance = Math.max(this.size * 2.15, 10);
        this.camera.position.set(this.center.x + distance, this.center.y - distance, this.center.z + distance * 0.72);
        this.camera.up.set(0, 0, 1);
        this.camera.near = Math.max(distance / 1000, 0.01);
        this.camera.far = Math.max(distance * 20, 1000);
        this.camera.updateProjectionMatrix();
        this.controls.target.copy(this.center);
        this.controls.update();
      }}

      applyCameraPreset(preset) {{
        const distance = Math.max(this.size * 2.2, 10);
        const c = this.center;
        const presets = {{
          fit: {{ position: [c.x + distance, c.y - distance, c.z + distance * 0.72], up: [0, 0, 1] }},
          iso: {{ position: [c.x + distance, c.y - distance, c.z + distance * 0.72], up: [0, 0, 1] }},
          xy: {{ position: [c.x, c.y, c.z + distance], up: [0, 1, 0] }},
          xy_rev: {{ position: [c.x, c.y, c.z - distance], up: [0, 1, 0] }},
          xz: {{ position: [c.x, c.y - distance, c.z], up: [0, 0, 1] }},
          xz_rev: {{ position: [c.x, c.y + distance, c.z], up: [0, 0, 1] }},
          yz: {{ position: [c.x + distance, c.y, c.z], up: [0, 0, 1] }},
          yz_rev: {{ position: [c.x - distance, c.y, c.z], up: [0, 0, 1] }},
        }};
        const selected = presets[preset] || presets.iso;
        const p = selected.position;
        const up = selected.up;
        const dampingWasEnabled = this.controls.enableDamping;
        this.controls.enableDamping = false;
        this.camera.position.set(p[0], p[1], p[2]);
        this.camera.up.set(up[0], up[1], up[2]);
        this.controls.target.copy(c);
        this.camera.lookAt(c);
        this.controls.update();
        this.controls.enableDamping = dampingWasEnabled;
        this.renderer.render(this.scene, this.camera);
      }}

      getCameraFrame(distanceMm) {{
        this.camera.updateMatrixWorld(true);
        const normal = new THREE.Vector3();
        this.camera.getWorldDirection(normal).normalize();
        const uAxis = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 0).normalize();
        const vAxis = new THREE.Vector3().crossVectors(normal, uAxis).normalize();
        const distance = Math.max(Number(distanceMm) || this.size * 0.25, 0.001);
        const target = this.controls.target.clone();
        const center = target.clone().addScaledVector(normal, -distance);
        return {{
          center: center.toArray(),
          normal: normal.toArray(),
          uAxis: uAxis.toArray(),
          vAxis: vAxis.toArray(),
          distanceMm: distance
        }};
      }}

      resize() {{
        const rect = this.container.getBoundingClientRect();
        const w = Math.max(1, Math.floor(rect.width));
        const h = Math.max(1, Math.floor(rect.height));
        this.camera.aspect = w / h;
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(w, h, true);
      }}

      animate() {{
        this.controls.update();
        this.renderer.render(this.scene, this.camera);
        requestAnimationFrame(this.animate);
      }}
    }}

    window.LeakageThreeViewer = {{
      create(container, mode) {{
        return new LeakageThreeViewer(container, mode);
      }}
    }};
    window.dispatchEvent(new Event('leakage-three-ready'));
  </script>

  <script>
    const initialBootToken = {json.dumps(SERVER_BOOT_TOKEN)};
    const demoCadPath = {json.dumps(str(DEMO_CAD_PATH))};
    const demoCadName = {json.dumps(DEMO_CAD_PATH.name)};
    const state = {{
      mesh: null,
      sceneToken: null,
      rayTraceResult: null,
      rayTraceRunning: false,
      rayDisplayFilters: {{
        receiver_direct: true,
        receiver_reflected: true,
        direct: true,
        specular: true,
        lambertian: true,
        gaussian: true,
      }},
      selectedFaces: new Set(),
      clickedFaces: new Set(),
      panelFaces: new Set(),
      selectedObjectIds: new Set(),
      faceToObjectId: new Map(),
      selectedGapObjectId: null,
      selectedGapObjectIds: new Set(),
      hiddenComponentObjectIds: new Set(),
      nonTraceableComponentObjectIds: new Set(),
      deletedComponentObjectIds: new Set(),
      componentContextMenuObjectId: null,
      componentContextMenuPosition: null,
      transformRules: [],
      activeTransformRuleId: null,
      selectedTransformRuleIds: new Set(),
      objectsById: new Map(),
      faceAdjacency: new Map(),
      roiSelectionMode: 'none',
      gapTargetMode: 'component_move_gap',
      gapSelectionMethod: 'click',
      viewerEngine: 'three',
      renderMode: 'wireframe',
      axisScalePercent: 100,
      inspectedFaceIndex: null,
      selectedGapFaces: new Set(),
      localGapFaces: new Set(),
      gapMove: {{ x: 0, y: 0, z: 0 }},
      gapMoveText: {{ x: '0', y: '0', z: '0' }},
      gapTilt: {{ x: 0, y: 0, z: 0 }},
      gapTiltText: {{ x: '0', y: '0', z: '0' }},
      previewOverlayEnabled: true,
      movePopupVisible: false,
      selectionBox: {{ active: false, additive: false, canvasMode: 'full', startX: 0, startY: 0, currentX: 0, currentY: 0 }},
      renderScenes: {{ full: null, roi: null }},
      transform: {{ yaw: 0.7, pitch: 0.4, distance: 1.8 }},
      sidebarLayout: 'vertical',
      activeSideTab: null,
      openSidePanels: new Set(),
      selectedMaterialObjectId: null,
      materialTargetMode: 'part',
      materialAssignments: [],
      materialBaseLibrary: [],
      materialSurfaceLibrary: [],
      materialOpticalProfiles: [],
      bsdfAssets: [],
      materialDraft: {{
        base_material_id: 'black_pc_resin',
        surface_id: 'matte_black_resin',
        profile_id: '',
        bsdf_asset_id: ''
      }},
      emitters: [],
      activeEmitterId: null,
      emitterDraftType: 'face',
      emitterDraftFaces: new Set(),
      emitterReferenceVertices: [],
      emitterReferenceEdges: [],
      emitterSelectionActive: false,
      emitterSequence: 1,
      emitterPopupPosition: null,
      emitterPopupDrag: {{ active: false, offsetX: 0, offsetY: 0 }},
      receivers: [],
      activeReceiverId: null,
      receiverDraftType: 'datum_plane',
      receiverReferenceVertices: [],
      receiverReferenceEdges: [],
      receiverCurrentViewPlane: null,
      receiverSelectionActive: false,
      receiverSequence: 1,
      receiverPopupPosition: null,
      receiverPopupDrag: {{ active: false, offsetX: 0, offsetY: 0 }},
      resultPopupPosition: null,
      resultPopupDrag: {{ active: false, offsetX: 0, offsetY: 0 }},
      resultPopupResize: {{ active: false, startX: 0, startY: 0, startWidth: 0, startHeight: 0 }},
      resultActiveTab: 'ray_summary',
      popupPosition: null,
      popupDrag: {{ active: false, offsetX: 0, offsetY: 0 }},
      materialPopupPosition: null,
      materialPopupDrag: {{ active: false, offsetX: 0, offsetY: 0 }}
    }};

    const cadInput = document.getElementById('cadPath');
    const cadFileName = document.getElementById('cadFileName');
    const cadFilePicker = document.getElementById('cadFilePicker');
    const sidebarNavShell = document.getElementById('sidebarNavShell');
    const sidebarLayoutToggle = document.getElementById('sidebarLayoutToggle');
    const sideTabBar = document.getElementById('sideTabBar');
    const importCadBtn = document.getElementById('importCad');
    const loadDemoCadBtn = document.getElementById('loadDemoCad');
    const useSampleBtn = document.getElementById('useSample');
    const objectList = document.getElementById('objectList');
    const componentContextMenu = document.getElementById('componentContextMenu');
    const componentContextMenuName = document.getElementById('componentContextMenuName');
    const componentContextMenuStatus = document.getElementById('componentContextMenuStatus');
    const componentContextVisibility = document.getElementById('componentContextVisibility');
    const componentContextTraceability = document.getElementById('componentContextTraceability');
    const componentContextPrimarySeparator = document.getElementById('componentContextPrimarySeparator');
    const componentContextMaterial = document.getElementById('componentContextMaterial');
    const componentContextTransform = document.getElementById('componentContextTransform');
    const roiInput = document.getElementById('roiFacesInput');
    const roiStat = document.getElementById('roiStat');
    const roiSelectionMode = document.getElementById('roiSelectionMode');
    const componentSelectBlock = document.getElementById('componentSelectBlock');
    const faceIndexBlock = document.getElementById('faceIndexBlock');
    const clearRoiBtn = document.getElementById('clearRoi');
    const roiModeHint = document.getElementById('roiModeHint');
    const cadMeta = document.getElementById('cadMeta');
    const kpiFaces = document.getElementById('kpiFaces');
    const kpiVerts = document.getElementById('kpiVerts');
    const kpiMode = document.getElementById('kpiMode');
    const runForm = document.getElementById('runForm');
    const runBtn = document.getElementById('runBtn');
    const resultPanel = document.getElementById('resultPanel');
    const resultPlaceholder = document.getElementById('resultPlaceholder');
    const openRayTraceResultBtn = document.getElementById('openRayTraceResultBtn');
    const rayTraceResultPopup = document.getElementById('rayTraceResultPopup');
    const rayTraceResultPopupHeader = document.getElementById('rayTraceResultPopupHeader');
    const rayTraceResultPopupClose = document.getElementById('rayTraceResultPopupClose');
    const rayTraceResultPopupStatus = document.getElementById('rayTraceResultPopupStatus');
    const rayTraceResultTabs = document.getElementById('rayTraceResultTabs');
    const rayTraceResultPopupBody = document.getElementById('rayTraceResultPopupBody');
    const rayTraceResultResizeHandle = document.getElementById('rayTraceResultResizeHandle');
    const directRunHint = document.getElementById('directRunHint');
    const rayTraceProgressPanel = document.getElementById('rayTraceProgressPanel');
    const rayTraceProgressLabel = document.getElementById('rayTraceProgressLabel');
    const rayTraceRemaining = document.getElementById('rayTraceRemaining');
    const rayTraceProgressFill = document.getElementById('rayTraceProgressFill');
    const rayTraceProgressCount = document.getElementById('rayTraceProgressCount');
    const rayTraceElapsed = document.getElementById('rayTraceElapsed');
    const rtMaxDepthInput = document.getElementById('rtMaxDepthInput');
    const rtTerminationModeInput = document.getElementById('rtTerminationModeInput');
    const rtMinEnergyInput = document.getElementById('rtMinEnergyInput');
    const rtContributionModeInput = document.getElementById('rtContributionModeInput');
    const rtSeedInput = document.getElementById('rtSeedInput');
    const rtMaxPathsInput = document.getElementById('rtMaxPathsInput');
    const rtKAbsInput = document.getElementById('rtKAbsInput');
    const rtKBrdfInput = document.getElementById('rtKBrdfInput');
    const rtStorePathsInput = document.getElementById('rtStorePathsInput');
    const rayDisplayPanel = document.getElementById('rayDisplayPanel');
    const rayDisplayCount = document.getElementById('rayDisplayCount');
    const rayReceiverOnlyBtn = document.getElementById('rayReceiverOnlyBtn');
    const rayAllOnBtn = document.getElementById('rayAllOnBtn');
    const rayAllOffBtn = document.getElementById('rayAllOffBtn');
    let lastRayTraceResultHtml = '';
    const materialTargetSummary = document.getElementById('materialTargetSummary');
    const materialBaseList = document.getElementById('materialBaseList');
    const materialSurfaceList = document.getElementById('materialSurfaceList');
    const materialProfileList = document.getElementById('materialProfileList');
    const materialAssignmentList = document.getElementById('materialAssignmentList');
    const materialAssignmentEmpty = document.getElementById('materialAssignmentEmpty');
    const newMaterialBtn = document.getElementById('newMaterialBtn');
    const newMaterialForm = document.getElementById('newMaterialForm');
    const newMaterialName = document.getElementById('newMaterialName');
    const newMaterialCategory = document.getElementById('newMaterialCategory');
    const newMaterialReflectance = document.getElementById('newMaterialReflectance');
    const newMaterialDefaultSurface = document.getElementById('newMaterialDefaultSurface');
    const saveNewMaterialBtn = document.getElementById('saveNewMaterialBtn');
    const cancelNewMaterialBtn = document.getElementById('cancelNewMaterialBtn');
    const newSurfaceBtn = document.getElementById('newSurfaceBtn');
    const newSurfaceForm = document.getElementById('newSurfaceForm');
    const cancelNewSurfaceBtn = document.getElementById('cancelNewSurfaceBtn');
    const newBsdfBtn = document.getElementById('newBsdfBtn');
    const newBsdfForm = document.getElementById('newBsdfForm');
    const cancelNewBsdfBtn = document.getElementById('cancelNewBsdfBtn');
    const customSurfaceName = document.getElementById('customSurfaceName');
    const customSurfaceScatter = document.getElementById('customSurfaceScatter');
    const customSurfaceReflectance = document.getElementById('customSurfaceReflectance');
    const customSurfaceSpecular = document.getElementById('customSurfaceSpecular');
    const customSurfaceDiffuse = document.getElementById('customSurfaceDiffuse');
    const customSurfaceRoughness = document.getElementById('customSurfaceRoughness');
    const customSurfaceScatterWidth = document.getElementById('customSurfaceScatterWidth');
    const registerCustomSurfaceBtn = document.getElementById('registerCustomSurfaceBtn');
    const bsdfFileInput = document.getElementById('bsdfFileInput');
    const bsdfFileName = document.getElementById('bsdfFileName');
    const registerBsdfBtn = document.getElementById('registerBsdfBtn');
    const bsdfAssetList = document.getElementById('bsdfAssetList');
    const emitterType = document.getElementById('emitterType');
    const emitterFace = document.getElementById('emitterFace');
    const emitterNormal = document.getElementById('emitterNormal');
    const emitterDirectionDistribution = document.getElementById('emitterDirectionDistribution');
    const emitterSpecsJson = document.getElementById('emitterSpecsJson');
    const addFaceEmitterBtn = document.getElementById('addFaceEmitterBtn');
    const addDatumEmitterBtn = document.getElementById('addDatumEmitterBtn');
    const addReferenceEmitterBtn = document.getElementById('addReferenceEmitterBtn');
    const emitterAddSection = document.getElementById('emitterAddSection');
    const emitterSelectionBanner = document.getElementById('emitterSelectionBanner');
    const emitterList = document.getElementById('emitterList');
    const emitterEmpty = document.getElementById('emitterEmpty');
    const emitterBoxMin = document.querySelector('input[name=\"emitter_box_min\"]');
    const emitterBoxMax = document.querySelector('input[name=\"emitter_box_max\"]');
    const emitterCenter = document.querySelector('input[name=\"emitter_sphere_center\"]');
    const emitterRadius = document.querySelector('input[name=\"emitter_sphere_radius\"]');
    const gapTargetMode = document.getElementById('gapTargetMode');
    const gapSelectionMethod = document.getElementById('gapSelectionMethod');
    const gapMode = document.getElementById('gapMode');
    const gapModeHint = document.getElementById('gapModeHint');
    const transformRulePanel = document.getElementById('transformRulePanel');
    const gapComponentIds = document.getElementById('gapComponentIds');
    const gapFaceIndices = document.getElementById('gapFaceIndices');
    const gapFaceInput = document.getElementById('gapFaceInput');
    const gapFaceSummary = document.getElementById('gapFaceSummary');
    const gapFacePanel = document.getElementById('gapFacePanel');
    const gapObjectList = document.getElementById('gapObjectList');
    const componentSelectionSummary = document.getElementById('componentSelectionSummary');
    const transformRuleList = document.getElementById('transformRuleList');
    const transformManagerEmpty = document.getElementById('transformManagerEmpty');
    const transformSelectionSummary = document.getElementById('transformSelectionSummary');
    const gapMoveCombined = document.getElementById('gapMoveCombined');
    const gapTiltCombined = document.getElementById('gapTiltCombined');
    const gapMoveX = document.getElementById('gapMoveX');
    const gapMoveY = document.getElementById('gapMoveY');
    const gapMoveZ = document.getElementById('gapMoveZ');
    const gapTiltX = document.getElementById('gapTiltX');
    const gapTiltY = document.getElementById('gapTiltY');
    const gapTiltZ = document.getElementById('gapTiltZ');
    const gapMoveSummary = document.getElementById('gapMoveSummary');
    const previewOverlayToggle = document.getElementById('previewOverlayToggle');
    const viewerEngineGroup = document.getElementById('viewerEngineGroup');
    const renderModeGroup = document.getElementById('renderModeGroup');
    const cameraPresetGroup = document.getElementById('cameraPresetGroup');
    const renderModeBadge = document.getElementById('renderModeBadge');
    const axisScale = document.getElementById('axisScale');
    const axisScaleValue = document.getElementById('axisScaleValue');
    const coordReadout = document.getElementById('coordReadout');
    const coordSummary = document.getElementById('coordSummary');
    const fullCanvas = document.getElementById('canvas3d');
    const roiCanvas = document.getElementById('roiCanvas');
    const threeFullViewer = document.getElementById('threeFullViewer');
    const threeRoiViewer = document.getElementById('threeRoiViewer');
    const viewerStage = document.getElementById('viewerStage');
    const fullViewerCard = document.querySelector('.viewer-card-full');
    const roiViewerCard = document.querySelector('.viewer-card-roi');
    const fullViewHint = document.getElementById('fullViewHint');
    const roiViewHint = document.getElementById('roiViewHint');
    const viewerTip = document.getElementById('viewerTip');
    const viewerMovePanel = document.getElementById('viewerMovePanel');
    const viewerMoveChip = document.getElementById('viewerMoveChip');
    const viewerMoveName = document.getElementById('viewerMoveName');
    const viewerMoveSummary = document.getElementById('viewerMoveSummary');
    const popupMoveX = document.getElementById('popupMoveX');
    const popupMoveY = document.getElementById('popupMoveY');
    const popupMoveZ = document.getElementById('popupMoveZ');
    const popupTiltX = document.getElementById('popupTiltX');
    const popupTiltY = document.getElementById('popupTiltY');
    const popupTiltZ = document.getElementById('popupTiltZ');
    const cursorMovePopup = document.getElementById('cursorMovePopup');
    const cursorMovePopupHeader = document.getElementById('cursorMovePopupHeader');
    const cursorMoveName = document.getElementById('cursorMoveName');
    const cursorMoveSummary = document.getElementById('cursorMoveSummary');
    const cursorMoveX = document.getElementById('cursorMoveX');
    const cursorMoveY = document.getElementById('cursorMoveY');
    const cursorMoveZ = document.getElementById('cursorMoveZ');
    const cursorTiltX = document.getElementById('cursorTiltX');
    const cursorTiltY = document.getElementById('cursorTiltY');
    const cursorTiltZ = document.getElementById('cursorTiltZ');
    const cursorApplyBtn = document.getElementById('cursorApplyBtn');
    const cursorResetBtn = document.getElementById('cursorResetBtn');
    const cursorRestoreBtn = document.getElementById('cursorRestoreBtn');
    const cursorMaterialPopup = document.getElementById('cursorMaterialPopup');
    const cursorMaterialPopupHeader = document.getElementById('cursorMaterialPopupHeader');
    const cursorMaterialClose = document.getElementById('cursorMaterialClose');
    const cursorMaterialOpenLibrary = document.getElementById('cursorMaterialOpenLibrary');
    const cursorMaterialChip = document.getElementById('cursorMaterialChip');
    const cursorMaterialName = document.getElementById('cursorMaterialName');
    const cursorMaterialSummary = document.getElementById('cursorMaterialSummary');
    const materialTargetMode = document.getElementById('materialTargetMode');
    const materialPopupBaseSelect = document.getElementById('materialPopupBaseSelect');
    const materialPopupSurfaceSelect = document.getElementById('materialPopupSurfaceSelect');
    const materialPopupProfileSelect = document.getElementById('materialPopupProfileSelect');
    const materialApplyBtn = document.getElementById('materialApplyBtn');
    const materialApplyFacesBtn = document.getElementById('materialApplyFacesBtn');

    let threeFullRenderer = null;
    let threeRoiRenderer = null;
    let pendingThreeCameraPreset = null;
    const materialSaveProfileBtn = document.getElementById('materialSaveProfileBtn');
    const cursorMoveClose = document.getElementById('cursorMoveClose');
    const cursorEmitterPopup = document.getElementById('cursorEmitterPopup');
    const cursorEmitterPopupHeader = document.getElementById('cursorEmitterPopupHeader');
    const cursorEmitterClose = document.getElementById('cursorEmitterClose');
    const cursorEmitterNameHint = document.getElementById('cursorEmitterNameHint');
    const cursorEmitterChip = document.getElementById('cursorEmitterChip');
    const emitterReselectFacesBtn = document.getElementById('emitterReselectFacesBtn');
    const emitterNameInput = document.getElementById('emitterNameInput');
    const emitterPowerModeSelect = document.getElementById('emitterPowerModeSelect');
    const emitterTotalPowerLabel = document.getElementById('emitterTotalPowerLabel');
    const emitterPowerDensityLabel = document.getElementById('emitterPowerDensityLabel');
    const emitterPowerInput = document.getElementById('emitterPowerInput');
    const emitterPowerDensityInput = document.getElementById('emitterPowerDensityInput');
    const emitterRayCountInput = document.getElementById('emitterRayCountInput');
    const emitterFaceCountInput = document.getElementById('emitterFaceCountInput');
    const emitterDistributionSelect = document.getElementById('emitterDistributionSelect');
    const emitterGaussianSigmaLabel = document.getElementById('emitterGaussianSigmaLabel');
    const emitterGaussianSigmaInput = document.getElementById('emitterGaussianSigmaInput');
    const emitterNormalFlipInput = document.getElementById('emitterNormalFlipInput');
    const emitterDatumSection = document.getElementById('emitterDatumSection');
    const emitterReferenceSection = document.getElementById('emitterReferenceSection');
    const emitterCenterX = document.getElementById('emitterCenterX');
    const emitterCenterY = document.getElementById('emitterCenterY');
    const emitterCenterZ = document.getElementById('emitterCenterZ');
    const emitterWidthInput = document.getElementById('emitterWidthInput');
    const emitterHeightInput = document.getElementById('emitterHeightInput');
    const emitterAreaInput = document.getElementById('emitterAreaInput');
    const emitterRotationX = document.getElementById('emitterRotationX');
    const emitterRotationY = document.getElementById('emitterRotationY');
    const emitterRotationZ = document.getElementById('emitterRotationZ');
    const emitterReferenceModeSelect = document.getElementById('emitterReferenceModeSelect');
    const emitterReferenceSurfaceWrap = document.getElementById('emitterReferenceSurfaceWrap');
    const emitterReferenceSurfaceSelect = document.getElementById('emitterReferenceSurfaceSelect');
    const emitterReferenceHint = document.getElementById('emitterReferenceHint');
    const emitterClearReferencesBtn = document.getElementById('emitterClearReferencesBtn');
    const emitterReferenceCount = document.getElementById('emitterReferenceCount');
    const emitterApplyBtn = document.getElementById('emitterApplyBtn');
    const emitterResetBtn = document.getElementById('emitterResetBtn');
    const emitterDeleteBtn = document.getElementById('emitterDeleteBtn');
    const emitterGeometrySummary = document.getElementById('emitterGeometrySummary');
    const addDatumReceiverBtn = document.getElementById('addDatumReceiverBtn');
    const addReferenceReceiverBtn = document.getElementById('addReferenceReceiverBtn');
    const addCurrentViewReceiverBtn = document.getElementById('addCurrentViewReceiverBtn');
    const receiverAddSection = document.getElementById('receiverAddSection');
    const receiverSelectionBanner = document.getElementById('receiverSelectionBanner');
    const receiverList = document.getElementById('receiverList');
    const receiverEmpty = document.getElementById('receiverEmpty');
    const receiverSpecsJson = document.getElementById('receiverSpecsJson');
    const cursorReceiverPopup = document.getElementById('cursorReceiverPopup');
    const cursorReceiverPopupHeader = document.getElementById('cursorReceiverPopupHeader');
    const cursorReceiverClose = document.getElementById('cursorReceiverClose');
    const cursorReceiverNameHint = document.getElementById('cursorReceiverNameHint');
    const cursorReceiverChip = document.getElementById('cursorReceiverChip');
    const receiverReselectGeometryBtn = document.getElementById('receiverReselectGeometryBtn');
    const receiverNameInput = document.getElementById('receiverNameInput');
    const receiverWidthInput = document.getElementById('receiverWidthInput');
    const receiverHeightInput = document.getElementById('receiverHeightInput');
    const receiverAreaInput = document.getElementById('receiverAreaInput');
    const receiverDatumSection = document.getElementById('receiverDatumSection');
    const receiverReferenceSection = document.getElementById('receiverReferenceSection');
    const receiverCurrentViewSection = document.getElementById('receiverCurrentViewSection');
    const receiverAdjustmentSection = document.getElementById('receiverAdjustmentSection');
    const receiverOffsetX = document.getElementById('receiverOffsetX');
    const receiverOffsetY = document.getElementById('receiverOffsetY');
    const receiverOffsetZ = document.getElementById('receiverOffsetZ');
    const receiverTiltX = document.getElementById('receiverTiltX');
    const receiverTiltY = document.getElementById('receiverTiltY');
    const receiverTiltZ = document.getElementById('receiverTiltZ');
    const receiverCenterX = document.getElementById('receiverCenterX');
    const receiverCenterY = document.getElementById('receiverCenterY');
    const receiverCenterZ = document.getElementById('receiverCenterZ');
    const receiverRotationX = document.getElementById('receiverRotationX');
    const receiverRotationY = document.getElementById('receiverRotationY');
    const receiverRotationZ = document.getElementById('receiverRotationZ');
    const receiverReferenceModeSelect = document.getElementById('receiverReferenceModeSelect');
    const receiverReferenceHint = document.getElementById('receiverReferenceHint');
    const receiverClearReferencesBtn = document.getElementById('receiverClearReferencesBtn');
    const receiverReferenceCount = document.getElementById('receiverReferenceCount');
    const receiverViewDistanceInput = document.getElementById('receiverViewDistanceInput');
    const receiverCaptureViewBtn = document.getElementById('receiverCaptureViewBtn');
    const receiverResolutionX = document.getElementById('receiverResolutionX');
    const receiverResolutionY = document.getElementById('receiverResolutionY');
    const receiverAcceptanceInput = document.getElementById('receiverAcceptanceInput');
    const receiverNormalFlipInput = document.getElementById('receiverNormalFlipInput');
    const receiverApplyBtn = document.getElementById('receiverApplyBtn');
    const receiverResetBtn = document.getElementById('receiverResetBtn');
    const receiverDeleteBtn = document.getElementById('receiverDeleteBtn');
    const receiverGeometrySummary = document.getElementById('receiverGeometrySummary');
    const viewerWrap = document.querySelector('.viewer-wrap');

    function cloneVector(vector) {{
      return {{
        x: Number(vector && vector.x) || 0,
        y: Number(vector && vector.y) || 0,
        z: Number(vector && vector.z) || 0
      }};
    }}

    function vectorMagnitude(vector) {{
      const x = Number(vector && vector.x) || 0;
      const y = Number(vector && vector.y) || 0;
      const z = Number(vector && vector.z) || 0;
      return Math.sqrt(x * x + y * y + z * z);
    }}

    function vectorEquals(a, b, epsilon) {{
      const tol = epsilon || 1e-9;
      return Math.abs((a?.x || 0) - (b?.x || 0)) <= tol
        && Math.abs((a?.y || 0) - (b?.y || 0)) <= tol
        && Math.abs((a?.z || 0) - (b?.z || 0)) <= tol;
    }}

    function transformRuleHasAppliedTransform(rule) {{
      if (!rule) return false;
      return vectorMagnitude(rule.move) > 1e-9 || vectorMagnitude(rule.tilt) > 1e-9;
    }}

    function activeEditorDiffersFromRule() {{
      const rule = activeTransformRule();
      if (!rule) return false;
      return !vectorEquals(state.gapMove, rule.move) || !vectorEquals(state.gapTilt, rule.tilt);
    }}

    function setResultMessage(text, options) {{
      if (resultPlaceholder) {{
        resultPlaceholder.style.display = 'none';
      }}
      resultPanel.style.display = 'block';
      resultPanel.innerHTML = text;
      if (options && options.openResult) {{
        switchSideTab('result', {{ forceOpen: true }});
      }}
    }}

    function clampRayTraceResultPopupPosition(left, top) {{
      const rect = viewerWrap.getBoundingClientRect();
      const popupWidth = rayTraceResultPopup.offsetWidth || 780;
      const popupHeight = rayTraceResultPopup.offsetHeight || 680;
      return {{
        left: Math.min(Math.max(16, rect.width - popupWidth - 16), Math.max(16, left)),
        top: Math.min(Math.max(60, rect.height - popupHeight - 16), Math.max(60, top))
      }};
    }}

    function applyRayTraceResultPopupPosition(left, top) {{
      const next = clampRayTraceResultPopupPosition(left, top);
      rayTraceResultPopup.style.left = next.left + 'px';
      rayTraceResultPopup.style.top = next.top + 'px';
      state.resultPopupPosition = next;
    }}

    function showRayTraceResultPopup() {{
      rayTraceResultPopup.classList.remove('hidden-block');
      if (state.resultPopupPosition) {{
        applyRayTraceResultPopupPosition(state.resultPopupPosition.left, state.resultPopupPosition.top);
      }} else {{
        const rect = viewerWrap.getBoundingClientRect();
        const popupWidth = rayTraceResultPopup.offsetWidth || 780;
        const popupHeight = rayTraceResultPopup.offsetHeight || 680;
        applyRayTraceResultPopupPosition(
          Math.max(16, (rect.width - popupWidth) / 2),
          Math.max(60, (rect.height - popupHeight) / 2)
        );
      }}
    }}

    function hideRayTraceResultPopup() {{
      rayTraceResultPopup.classList.add('hidden-block');
    }}

    function startRayTraceResultPopupDrag(ev) {{
      if (ev.target && ev.target.closest('button')) return;
      const popupRect = rayTraceResultPopup.getBoundingClientRect();
      state.resultPopupDrag.active = true;
      state.resultPopupDrag.offsetX = ev.clientX - popupRect.left;
      state.resultPopupDrag.offsetY = ev.clientY - popupRect.top;
      rayTraceResultPopup.classList.add('is-dragging');
      ev.preventDefault();
    }}

    function moveRayTraceResultPopupDrag(ev) {{
      if (!state.resultPopupDrag.active) return;
      const rect = viewerWrap.getBoundingClientRect();
      applyRayTraceResultPopupPosition(
        ev.clientX - rect.left - state.resultPopupDrag.offsetX,
        ev.clientY - rect.top - state.resultPopupDrag.offsetY
      );
    }}

    function stopRayTraceResultPopupDrag() {{
      if (state.resultPopupDrag.active) {{
        state.resultPopupDrag.active = false;
        rayTraceResultPopup.classList.remove('is-dragging');
      }}
      if (!rayTraceResultPopup.classList.contains('hidden-block') && state.resultPopupPosition) {{
        applyRayTraceResultPopupPosition(state.resultPopupPosition.left, state.resultPopupPosition.top);
      }}
    }}

    function startRayTraceResultPopupResize(ev) {{
      const popupRect = rayTraceResultPopup.getBoundingClientRect();
      state.resultPopupResize.active = true;
      state.resultPopupResize.startX = ev.clientX;
      state.resultPopupResize.startY = ev.clientY;
      state.resultPopupResize.startWidth = popupRect.width;
      state.resultPopupResize.startHeight = popupRect.height;
      rayTraceResultPopup.classList.add('is-resizing');
      ev.preventDefault();
      ev.stopPropagation();
    }}

    function moveRayTraceResultPopupResize(ev) {{
      if (!state.resultPopupResize.active) return;
      const viewerRect = viewerWrap.getBoundingClientRect();
      const popupLeft = state.resultPopupPosition?.left || 16;
      const popupTop = state.resultPopupPosition?.top || 60;
      const maxWidth = Math.max(320, viewerRect.width - popupLeft - 16);
      const maxHeight = Math.max(260, viewerRect.height - popupTop - 16);
      const minWidth = Math.min(520, maxWidth);
      const minHeight = Math.min(360, maxHeight);
      const nextWidth = Math.min(
        maxWidth,
        Math.max(minWidth, state.resultPopupResize.startWidth + ev.clientX - state.resultPopupResize.startX)
      );
      const nextHeight = Math.min(
        maxHeight,
        Math.max(minHeight, state.resultPopupResize.startHeight + ev.clientY - state.resultPopupResize.startY)
      );
      rayTraceResultPopup.style.width = Math.round(nextWidth) + 'px';
      rayTraceResultPopup.style.height = Math.round(nextHeight) + 'px';
      ev.preventDefault();
    }}

    function stopRayTraceResultPopupResize() {{
      if (!state.resultPopupResize.active) return;
      state.resultPopupResize.active = false;
      rayTraceResultPopup.classList.remove('is-resizing');
      if (state.resultPopupPosition) {{
        applyRayTraceResultPopupPosition(state.resultPopupPosition.left, state.resultPopupPosition.top);
      }}
    }}

    function setRayTraceResultTab(tabName) {{
      const allowedTabs = new Set(['ray_summary', 'surface_optical', 'multi_bounce', 'receiver']);
      const nextTab = allowedTabs.has(tabName) ? tabName : 'ray_summary';
      state.resultActiveTab = nextTab;
      for (const button of rayTraceResultTabs.querySelectorAll('[data-result-tab]')) {{
        const active = button.getAttribute('data-result-tab') === nextTab;
        button.classList.toggle('active', active);
        button.setAttribute('aria-selected', active ? 'true' : 'false');
        button.tabIndex = active ? 0 : -1;
      }}
      for (const panel of rayTraceResultPopupBody.querySelectorAll('[data-result-tab-panel]')) {{
        const active = panel.getAttribute('data-result-tab-panel') === nextTab;
        panel.classList.toggle('active', active);
        panel.setAttribute('aria-hidden', active ? 'false' : 'true');
      }}
      rayTraceResultPopupBody.scrollTop = 0;
    }}

    function renderRayTraceResultPopup(resultHtml, result) {{
      rayTraceResultPopupBody.innerHTML = resultHtml;
      setRayTraceResultTab('ray_summary');
      rayTraceResultPopupStatus.textContent = 'Complete · ' + new Date().toLocaleTimeString('ko-KR');
      showRayTraceResultPopup();
      drawReceiverHeatmaps(result, rayTraceResultPopupBody);
    }}

    function escapeHtml(value) {{
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
    }}

    function formatRayMetric(value) {{
      const numeric = Number(value) || 0;
      const magnitude = Math.abs(numeric);
      if (magnitude > 0 && (magnitude >= 10000 || magnitude < 0.001)) return numeric.toExponential(3);
      return numeric.toFixed(3);
    }}

    function updateRayTraceRunState() {{
      const emitterCount = state.emitters.filter((item) => item.enabled !== false).length;
      const receiverCount = state.receivers.filter((item) => item.enabled !== false).length;
      const ready = !!state.mesh && !!state.sceneToken && emitterCount > 0 && receiverCount > 0 && !state.rayTraceRunning;
      runBtn.disabled = !ready;
      runBtn.textContent = state.rayTraceRunning ? 'Tracing rays…' : 'Run ray tracing';
      directRunHint.classList.toggle('ready', ready || state.rayTraceRunning);
      if (state.rayTraceRunning) {{
        directRunHint.textContent = 'Emitter → Receiver direct ray를 계산하고 있습니다.';
      }} else if (!state.mesh || !state.sceneToken) {{
        directRunHint.textContent = '먼저 CAD model을 불러오세요.';
      }} else if (!emitterCount || !receiverCount) {{
        directRunHint.textContent = 'Emitter ' + emitterCount + '개 · Receiver ' + receiverCount + '개 — 각각 1개 이상 필요합니다.';
      }} else {{
        const rayCount = state.emitters
          .filter((item) => item.enabled !== false)
          .reduce((sum, item) => sum + Math.max(1, Number(item.ray_count) || 1), 0);
        directRunHint.textContent = 'Ready · Emitter ' + emitterCount + ' · Receiver ' + receiverCount + ' · Total rays ' + rayCount.toLocaleString();
      }}
    }}

    function drawReceiverHeatmaps(result, root) {{
      const heatmapRoot = root || resultPanel;
      if (!heatmapRoot) return;
      for (const grid of result.receiver_grids || []) {{
        const canvas = heatmapRoot.querySelector('[data-receiver-heatmap="' + grid.receiver_id + '"]');
        if (!canvas) continue;
        const columns = Math.max(1, Number(grid.resolution?.[0]) || 1);
        const rows = Math.max(1, Number(grid.resolution?.[1]) || 1);
        canvas.width = columns;
        canvas.height = rows;
        const context = canvas.getContext('2d');
        const image = context.createImageData(columns, rows);
        const values = (grid.flux_lumen || []).flat().map((value) => Math.max(0, Number(value) || 0));
        const peak = Math.max(...values, 0);
        for (let index = 0; index < columns * rows; index += 1) {{
          const normalized = peak > 0 ? Math.sqrt((values[index] || 0) / peak) : 0;
          let red = 2;
          let green = 6;
          let blue = 23;
          if (normalized > 0) {{
            if (normalized < 0.35) {{
              const scale = normalized / 0.35;
              red = 2 + 28 * scale; green = 6 + 58 * scale; blue = 23 + 152 * scale;
            }} else if (normalized < 0.7) {{
              const scale = (normalized - 0.35) / 0.35;
              red = 30 - 24 * scale; green = 64 + 118 * scale; blue = 175 + 37 * scale;
            }} else {{
              const scale = (normalized - 0.7) / 0.3;
              red = 6 + 249 * scale; green = 182 + 73 * scale; blue = 212 - 191 * scale;
            }}
          }}
          const pixelIndex = index * 4;
          image.data[pixelIndex] = Math.round(red);
          image.data[pixelIndex + 1] = Math.round(green);
          image.data[pixelIndex + 2] = Math.round(blue);
          image.data[pixelIndex + 3] = 255;
        }}
        context.putImageData(image, 0, 0);
      }}
    }}

    function rayPathReachesReceiver(path) {{
      return Array.isArray(path)
        && path.length >= 2
        && path[path.length - 1]?.event_type === 'receiver';
    }}

    function receiverPathFilterName(path) {{
      if (!rayPathReachesReceiver(path)) return null;
      return path.some((event) => event?.event_type === 'surface')
        ? 'receiver_reflected'
        : 'receiver_direct';
    }}

    function rayPathHasVisibleNonReceiverSegment(path) {{
      if (!Array.isArray(path) || path.length < 2 || rayPathReachesReceiver(path)) return false;
      if (state.rayDisplayFilters.direct) return true;
      for (let index = 1; index < path.length; index++) {{
        const startEvent = path[index - 1];
        const endEvent = path[index];
        const rayKind = String(endEvent?.ray_kind || startEvent?.ray_kind || '');
        if (rayKind && state.rayDisplayFilters[rayKind]) return true;
      }}
      return false;
    }}

    function updateRayDisplayUI() {{
      if (!rayDisplayPanel || !rayDisplayCount) return;
      const paths = state.rayTraceResult && Array.isArray(state.rayTraceResult.stored_paths)
        ? state.rayTraceResult.stored_paths
        : [];
      let visiblePathCount = 0;
      for (const path of paths) {{
        if (rayPathReachesReceiver(path)) {{
          const receiverFilter = receiverPathFilterName(path);
          if (receiverFilter && state.rayDisplayFilters[receiverFilter]) visiblePathCount += 1;
        }} else if (rayPathHasVisibleNonReceiverSegment(path)) {{
          visiblePathCount += 1;
        }}
      }}
      rayDisplayCount.textContent = visiblePathCount + '/' + paths.length;
      rayDisplayPanel.classList.toggle('hidden-block', paths.length === 0);
      for (const input of rayDisplayPanel.querySelectorAll('[data-ray-filter]')) {{
        const filterName = input.getAttribute('data-ray-filter');
        input.checked = !!state.rayDisplayFilters[filterName];
      }}
    }}

    function applyRayDisplayPreset(preset) {{
      const enableAll = preset === 'all';
      state.rayDisplayFilters.receiver_direct = enableAll || preset === 'receiver';
      state.rayDisplayFilters.receiver_reflected = enableAll || preset === 'receiver';
      state.rayDisplayFilters.direct = enableAll;
      state.rayDisplayFilters.specular = enableAll;
      state.rayDisplayFilters.lambertian = enableAll;
      state.rayDisplayFilters.gaussian = enableAll;
      updateRayDisplayUI();
      drawViewer();
    }}

    function renderDirectRayTraceResult(result) {{
      const totalRays = Math.max(0, Number(result.total_rays) || 0);
      const hitCount = Math.max(0, Number(result.receiver_hit_count) || 0);
      const surfaceInteractionCount = Math.max(0, Number(result.surface_hit_count) || 0);
      const reflectionSummary = result.metrics?._reflection_summary || {{}};
      const maxReflectionDepth = Math.max(0, Number(reflectionSummary.implemented_max_depth) || 0);
      const performanceSummary = result.metrics?._performance_summary || {{}};
      const intersectionBackend = String(performanceSummary.intersection_backend || 'brute_force');
      const primarySurfaceCount = Math.max(0, Number(reflectionSummary.primary_surface_hit_count) || 0);
      const hitRatio = totalRays > 0 ? hitCount / totalRays : 0;
      const primarySurfaceRatio = totalRays > 0 ? primarySurfaceCount / totalRays : 0;
      let receiverHtml = '';
      for (const receiver of result.receivers || []) {{
        const metrics = result.metrics?.[receiver.receiver_id] || {{}};
        const grid = (result.receiver_grids || []).find((item) => item.receiver_id === receiver.receiver_id);
        receiverHtml += '<div class="rt-receiver-result">'
          + '<div class="rt-receiver-title">' + escapeHtml(receiver.display_name || receiver.receiver_id) + '</div>'
          + '<div class="rt-metric-row">'
          + '<div>Peak nit_est<b>' + formatRayMetric(metrics.peak_nit_est) + '</b></div>'
          + '<div>Mean nit_est<b>' + formatRayMetric(metrics.mean_nit_est) + '</b></div>'
          + '<div>P95 nit_est<b>' + formatRayMetric(metrics.p95_nit_est) + '</b></div>'
          + '</div>'
          + '<div class="rt-metric-row">'
          + '<div>Hits<b>' + Math.round(Number(metrics.hit_count) || 0).toLocaleString() + '</b></div>'
          + '<div>Flux (lm)<b>' + formatRayMetric(metrics.total_flux_lumen) + '</b></div>'
          + '<div>Lit area<b>' + formatRayMetric(metrics.area_above_zero_mm2) + ' mm²</b></div>'
          + '</div>'
          + (grid ? '<canvas class="rt-heatmap" data-receiver-heatmap="' + escapeHtml(receiver.receiver_id) + '"></canvas>' : '')
          + '<div class="rt-result-note">Heatmap은 Receiver grid에 누적된 direct + 최대 ' + maxReflectionDepth + '회 반사 flux의 상대 분포입니다.</div>'
          + '</div>';
      }}
      const pathCount = Array.isArray(result.stored_paths) ? result.stored_paths.length : 0;
      const opticalSummary = result.metrics?._optical_summary || {{}};
      const opticalProfiles = Object.values(opticalSummary.profile_hits || {{}})
        .sort((left, right) => Number(right.hit_count || 0) - Number(left.hit_count || 0));
      const unassignedCount = Math.max(0, Number(opticalSummary.unassigned_surface_hit_count) || 0);
      let opticalHtml = '<div class="rt-receiver-result">'
        + '<div class="rt-receiver-title">Surface optical property lookup</div>'
        + '<div class="rt-metric-row">'
        + '<div>Surface hits<b>' + Math.round(Number(opticalSummary.surface_hit_count) || 0).toLocaleString() + '</b></div>'
        + '<div>Resolved<b>' + Math.max(0, Math.round((Number(opticalSummary.surface_hit_count) || 0) - unassignedCount)).toLocaleString() + '</b></div>'
        + '<div>Unassigned<b>' + unassignedCount.toLocaleString() + '</b></div>'
        + '</div>'
        + opticalProfiles.slice(0, 8).map(item =>
          '<div class="library-row"><div class="name">' + escapeHtml(item.profile_id || '-') + '</div>'
          + '<div class="meta">source: ' + escapeHtml(item.source || '-')
          + '\\nhits: ' + Math.round(Number(item.hit_count) || 0).toLocaleString()
          + ' / R: ' + Number(item.reflectance || 0).toFixed(3)
          + '\\nmodel: ' + escapeHtml(item.scatter_model || '-')
          + ' / spec: ' + Number(item.specular_ratio || 0).toFixed(2)
          + ' / diffuse: ' + Number(item.diffuse_ratio || 0).toFixed(2)
          + '\\npotential reflected flux: ' + formatRayMetric(item.potential_reflected_flux_lumen) + ' lm</div></div>'
        ).join('')
        + '<div class="rt-result-note">Potential reflected flux는 R × 입사 flux이며 다음 bounce ray의 시작 광량으로 사용됩니다.</div>'
        + '</div>';
      const reflectionLobes = reflectionSummary.lobes || {{}};
      const reflectionHtml = '<div class="rt-receiver-result">'
        + '<div class="rt-receiver-title">Multi-bounce reflection · max depth ' + maxReflectionDepth + '</div>'
        + '<div class="rt-metric-row">'
        + '<div>Emitted<b>' + Math.round(Number(reflectionSummary.reflection_emitted_count) || 0).toLocaleString() + '</b></div>'
        + '<div>Receiver hits<b>' + Math.round(Number(reflectionSummary.reflection_receiver_hit_count) || 0).toLocaleString() + '</b></div>'
        + '<div>Blocked<b>' + Math.round(Number(reflectionSummary.reflection_blocked_count) || 0).toLocaleString() + '</b></div>'
        + '</div>'
        + '<div class="rt-metric-row">'
        + '<div>Escaped<b>' + Math.round(Number(reflectionSummary.reflection_escaped_count) || 0).toLocaleString() + '</b></div>'
        + '<div>Direct flux<b>' + formatRayMetric(reflectionSummary.direct_receiver_flux_lumen) + ' lm</b></div>'
        + '<div>Reflected flux<b>' + formatRayMetric(reflectionSummary.reflected_receiver_flux_lumen) + ' lm</b></div>'
        + '</div>'
        + ['specular', 'gaussian', 'lambertian'].map(rayKind => {{
          const item = reflectionLobes[rayKind] || {{}};
          return '<div class="library-row"><div class="name">' + rayKind + '</div>'
            + '<div class="meta">emitted: ' + Math.round(Number(item.emitted_count) || 0).toLocaleString()
            + ' / receiver: ' + Math.round(Number(item.receiver_hit_count) || 0).toLocaleString()
            + '\\nemitted flux: ' + formatRayMetric(item.emitted_flux_lumen) + ' lm'
            + ' / received: ' + formatRayMetric(item.receiver_flux_lumen) + ' lm'
            + '\\nblocked: ' + Math.round(Number(item.blocked_count) || 0).toLocaleString()
            + ' / continued: ' + Math.round(Number(item.continued_count) || 0).toLocaleString()
            + ' / escaped: ' + Math.round(Number(item.escaped_count) || 0).toLocaleString()
            + '</div></div>';
        }}).join('')
        + '<div class="rt-result-note">메인 화면 Result의 3D Ray path 표시에서 경로 유형별 체크박스를 켜고 끌 수 있습니다. 기본값은 모든 저장 경로 표시입니다.</div>'
        + '</div>';
      const raySummaryHtml = '<div class="rt-result-kpis">'
        + '<div class="rt-result-kpi"><span>Total rays</span><strong>' + totalRays.toLocaleString() + '</strong></div>'
        + '<div class="rt-result-kpi"><span>Receiver hits</span><strong>' + hitCount.toLocaleString() + '</strong></div>'
        + '<div class="rt-result-kpi"><span>Surface interactions</span><strong>' + surfaceInteractionCount.toLocaleString() + '</strong></div>'
        + '<div class="rt-result-kpi"><span>Hit ratio</span><strong>' + (hitRatio * 100).toFixed(3) + '%</strong></div>'
        + '<div class="rt-result-kpi"><span>Primary surface ratio</span><strong>' + (primarySurfaceRatio * 100).toFixed(3) + '%</strong></div>'
        + '<div class="rt-result-kpi"><span>Runtime</span><strong>' + (Number(result.runtime_sec) || 0).toFixed(3) + ' s</strong></div>'
        + '<div class="rt-result-kpi"><span>Ray rate</span><strong>' + Math.round(Number(performanceSummary.rays_per_sec) || 0).toLocaleString() + ' /s</strong></div>'
        + '<div class="rt-result-kpi"><span>CAD intersection</span><strong>' + escapeHtml(intersectionBackend.toUpperCase()) + '</strong></div>'
        + '</div>'
        + '<div class="rt-result-note">RT-3 multi-bounce · PERF-2 CAD intersection · '
        + escapeHtml(intersectionBackend.toUpperCase())
        + ' · BVH build ' + (Number(performanceSummary.bvh_build_sec) || 0).toFixed(3)
        + ' s · 3D path ' + pathCount + '개 표시</div>';
      const resultHtml = '<section id="rayResultPanelSummary" class="ray-result-tab-panel active" data-result-tab-panel="ray_summary" role="tabpanel" aria-labelledby="rayResultTabSummary">'
        + '<h3 class="ray-result-section-heading">Ray summary</h3>'
        + raySummaryHtml
        + '</section>'
        + '<section id="rayResultPanelOptical" class="ray-result-tab-panel" data-result-tab-panel="surface_optical" role="tabpanel" aria-labelledby="rayResultTabOptical">'
        + opticalHtml
        + '</section>'
        + '<section id="rayResultPanelBounce" class="ray-result-tab-panel" data-result-tab-panel="multi_bounce" role="tabpanel" aria-labelledby="rayResultTabBounce">'
        + reflectionHtml
        + '</section>'
        + '<section id="rayResultPanelReceiver" class="ray-result-tab-panel" data-result-tab-panel="receiver" role="tabpanel" aria-labelledby="rayResultTabReceiver">'
        + '<h3 class="ray-result-section-heading">Receiver results</h3>'
        + (receiverHtml || '<div class="rt-result-note">Receiver result가 없습니다.</div>')
        + '</section>';
      lastRayTraceResultHtml = resultHtml;
      openRayTraceResultBtn.disabled = false;
      openRayTraceResultBtn.textContent = '분석 결과 보기';
      setResultMessage('<div><b>Ray tracing 완료</b><br>상세 분석 결과가 3D viewer 위 결과창에 표시되었습니다.</div>');
      renderRayTraceResultPopup(resultHtml, result);
    }}

    function invalidateDirectRayTraceResult() {{
      if (!state.rayTraceResult) return;
      state.rayTraceResult = null;
      lastRayTraceResultHtml = '';
      openRayTraceResultBtn.disabled = true;
      openRayTraceResultBtn.textContent = '분석 결과 보기';
      updateRayDisplayUI();
      setResultMessage('<div>모델, 광학 또는 Emitter/Receiver 설정이 변경되었습니다. Ray tracing을 다시 실행하세요.</div>');
      rayTraceResultPopupBody.innerHTML = '';
      hideRayTraceResultPopup();
    }}

    function formatRayTraceDuration(seconds) {{
      const totalSeconds = Math.max(0, Math.round(Number(seconds) || 0));
      if (totalSeconds < 60) return totalSeconds + 's';
      const minutes = Math.floor(totalSeconds / 60);
      const remainder = totalSeconds % 60;
      if (minutes < 60) return minutes + 'm' + (remainder ? ' ' + remainder + 's' : '');
      const hours = Math.floor(minutes / 60);
      const remainingMinutes = minutes % 60;
      return hours + 'h' + (remainingMinutes ? ' ' + remainingMinutes + 'm' : '');
    }}

    function updateRayTraceProgress(job) {{
      const status = String(job?.status || 'queued');
      const phase = String(job?.phase || status);
      const processed = Math.max(0, Number(job?.processed_rays) || 0);
      const total = Math.max(0, Number(job?.total_rays) || 0);
      const progress = Math.max(0, Math.min(1, Number(job?.progress) || 0));
      const elapsed = Math.max(0, Number(job?.elapsed_sec) || 0);
      const remaining = job?.estimated_remaining_sec;
      const phaseLabels = {{
        queued: '계산 대기 중',
        preparing: 'CAD / 광학 입력 준비 중',
        tracing: 'Ray tracing 진행 중',
        completed: 'Ray tracing 완료',
        failed: 'Ray tracing 실패',
      }};
      rayTraceProgressPanel.classList.remove('hidden-block', 'preparing', 'failed');
      if (phase === 'queued' || phase === 'preparing') rayTraceProgressPanel.classList.add('preparing');
      if (status === 'failed') rayTraceProgressPanel.classList.add('failed');
      rayTraceProgressLabel.textContent = phaseLabels[phase] || phaseLabels[status] || 'Ray tracing 진행 중';
      rayTraceProgressFill.style.width = (phase === 'queued' || phase === 'preparing')
        ? '28%'
        : (progress * 100).toFixed(1) + '%';
      rayTraceProgressCount.textContent = processed.toLocaleString() + ' / ' + total.toLocaleString() + ' rays';
      rayTraceElapsed.textContent = '경과 ' + formatRayTraceDuration(elapsed);
      if (status === 'completed') {{
        rayTraceRemaining.textContent = '완료';
      }} else if (status === 'failed') {{
        rayTraceRemaining.textContent = '중단됨';
      }} else if (remaining === null || remaining === undefined || processed <= 0) {{
        rayTraceRemaining.textContent = '예상 시간 계산 중';
      }} else {{
        rayTraceRemaining.textContent = '약 ' + formatRayTraceDuration(remaining) + ' 남음';
      }}
    }}

    async function waitForRayTraceJob(jobId) {{
      while (true) {{
        const response = await fetch('/api/raytrace/status?job_id=' + encodeURIComponent(jobId), {{ cache: 'no-store' }});
        const job = await response.json();
        if (!response.ok) throw new Error(job.error || 'Ray tracing progress lookup failed');
        updateRayTraceProgress(job);
        if (job.status === 'completed') return job.result;
        if (job.status === 'failed') throw new Error(job.error || 'Ray tracing failed');
        await new Promise((resolve) => window.setTimeout(resolve, 300));
      }}
    }}

    async function runDirectRayTrace() {{
      updateRayTraceRunState();
      if (runBtn.disabled || state.rayTraceRunning) return;
      state.rayTraceRunning = true;
      lastRayTraceResultHtml = '';
      openRayTraceResultBtn.disabled = true;
      openRayTraceResultBtn.textContent = '계산 완료 후 결과 보기';
      hideRayTraceResultPopup();
      updateRayTraceRunState();
      updateRayTraceProgress({{ status: 'queued', phase: 'queued', processed_rays: 0, total_rays: 0, progress: 0 }});
      setResultMessage('<div>Direct + multi-bounce ray tracing 실행 중…<br>진행 상황은 왼쪽 게이지에서 확인할 수 있습니다.</div>');
      try {{
        const totalRayCount = state.emitters
          .filter((item) => item.enabled !== false)
          .reduce((sum, item) => sum + Math.max(1, Number(item.ray_count) || 1), 0);
        const opticalPayload = buildRayTraceOpticalPayload();
        const requestPayload = {{
          scene_token: state.sceneToken,
          project_name: 'TV-Leakage-Direct',
          emitters: state.emitters.filter((item) => item.enabled !== false).map(emitterSpecPayload),
          receivers: state.receivers.filter((item) => item.enabled !== false).map(receiverSpecPayload),
          optical_profiles: opticalPayload.profiles,
          optical_assignments: opticalPayload.assignments,
          transform_rules: state.transformRules.filter((rule) => rule.enabled !== false),
          excluded_component_ids: getRayTraceExcludedComponentIds(),
          config: {{
            ray_count: Math.max(1, totalRayCount),
            max_depth: Math.max(0, Math.min(3, parseInt(rtMaxDepthInput.value, 10) || 0)),
            seed: parseInt(rtSeedInput.value, 10) || 42,
            min_energy: Math.max(0, Number(rtMinEnergyInput.value) || 0),
            epsilon_mm: 1e-4,
            k_abs: Math.max(0, Number(rtKAbsInput.value) || 0),
            k_brdf: Math.max(0, Number(rtKBrdfInput.value) || 0),
            termination_mode: rtTerminationModeInput.value === 'russian_roulette' ? 'russian_roulette' : 'threshold',
            contribution_mode: rtContributionModeInput.value === 'detailed' ? 'detailed' : 'summary',
            store_ray_paths: !!rtStorePathsInput.checked,
            max_stored_paths: Math.max(0, Math.min(1000, parseInt(rtMaxPathsInput.value, 10) || 0))
          }}
        }};
        const response = await fetch('/api/raytrace/start', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(requestPayload)
        }});
        const job = await response.json();
        if (!response.ok) throw new Error(job.error || 'Ray tracing failed');
        updateRayTraceProgress(job);
        const result = await waitForRayTraceJob(job.job_id);
        state.rayTraceResult = result;
        renderDirectRayTraceResult(result);
        updateRayDisplayUI();
        drawViewer();
      }} catch (error) {{
        state.rayTraceResult = null;
        lastRayTraceResultHtml = '';
        openRayTraceResultBtn.disabled = true;
        openRayTraceResultBtn.textContent = '분석 결과 보기';
        updateRayDisplayUI();
        updateRayTraceProgress({{ status: 'failed', phase: 'failed', processed_rays: 0, total_rays: 0, progress: 0 }});
        hideRayTraceResultPopup();
        setResultMessage('<div><b>Ray tracing failed:</b> ' + escapeHtml(error.message) + '</div>');
        drawViewer();
      }} finally {{
        state.rayTraceRunning = false;
        updateRayTraceRunState();
      }}
    }}

    function syncSidePanels() {{
      const buttons = sidebarNavShell.querySelectorAll('[data-side-tab]');
      const panels = runForm.querySelectorAll('[data-side-panel]');
      for (const button of buttons) {{
        const tab = button.getAttribute('data-side-tab');
        const isActive = state.sidebarLayout === 'horizontal'
          ? state.activeSideTab === tab
          : state.openSidePanels.has(tab);
        button.classList.toggle('active', isActive);
      }}
      for (const panel of panels) {{
        const tab = panel.getAttribute('data-side-panel');
        const isActive = state.sidebarLayout === 'horizontal'
          ? state.activeSideTab === tab
          : state.openSidePanels.has(tab);
        panel.classList.toggle('active', isActive);
      }}
    }}

    function switchSideTab(tabName, options) {{
      const forceOpen = options && Object.prototype.hasOwnProperty.call(options, 'forceOpen')
        ? !!options.forceOpen
        : null;
      state.activeSideTab = tabName;
      if (state.sidebarLayout === 'horizontal') {{
        syncSidePanels();
        return;
      }}
      const isOpen = state.openSidePanels.has(tabName);
      const nextOpen = forceOpen === null ? !isOpen : forceOpen;
      if (nextOpen) {{
        state.openSidePanels.add(tabName);
      }} else {{
        state.openSidePanels.delete(tabName);
      }}
      syncSidePanels();
    }}

    function setSidebarLayout(mode) {{
      state.sidebarLayout = mode === 'horizontal' ? 'horizontal' : 'vertical';
      sidebarNavShell.setAttribute('data-layout', state.sidebarLayout);
      const buttons = sidebarLayoutToggle.querySelectorAll('[data-layout]');
      for (const button of buttons) {{
        button.classList.toggle('active', button.getAttribute('data-layout') === state.sidebarLayout);
      }}
      syncSidePanels();
    }}

    function initDevAutoRefresh() {{
      let lastToken = initialBootToken;
      window.setInterval(async function () {{
        try {{
          const res = await fetch('/dev-status', {{ cache: 'no-store' }});
          if (!res.ok) return;
          const payload = await res.json();
          if (payload.boot_token && payload.boot_token !== lastToken) {{
            window.location.reload();
          }}
          lastToken = payload.boot_token || lastToken;
        }} catch (err) {{
        }}
      }}, 1200);
    }}

    function parseFaceList(raw) {{
      if (!raw) return [];
      const values = raw.split(',').map(x => x.trim()).filter(x => x !== '');
      const out = [];
      for (const value of values) {{
        const n = parseInt(value, 10);
        if (!isNaN(n)) out.push(n);
      }}
      return out;
    }}

    function uniqueSorted(arr) {{
      return Array.from(new Set(arr)).sort((a, b) => a - b);
    }}

    function objectLabel(item) {{
      const trunc = item.is_truncated ? ' (partially shown)' : '';
      return item.object_name + ' / faces: ' + item.face_count + ', area: ' + item.area_mm2 + ' mm2' + trunc;
    }}

    function isComponentHidden(objectId) {{
      return objectId !== null
        && objectId !== undefined
        && state.hiddenComponentObjectIds.has(objectId);
    }}

    function isComponentDeleted(objectId) {{
      return objectId !== null
        && objectId !== undefined
        && state.deletedComponentObjectIds.has(objectId);
    }}

    function isComponentTraceable(objectId) {{
      return objectId !== null
        && objectId !== undefined
        && !state.nonTraceableComponentObjectIds.has(objectId);
    }}

    function getRayTraceExcludedComponentIds() {{
      return uniqueSorted(
        Array.from(state.deletedComponentObjectIds).concat(Array.from(state.nonTraceableComponentObjectIds))
      );
    }}

    function isComponentUnavailable(objectId) {{
      return isComponentHidden(objectId) || isComponentDeleted(objectId);
    }}

    function isFaceDeleted(faceIndex) {{
      if (faceIndex === null || faceIndex === undefined) return false;
      return isComponentDeleted(state.faceToObjectId.get(faceIndex));
    }}

    function isFaceHidden(faceIndex) {{
      if (faceIndex === null || faceIndex === undefined) return false;
      return isComponentUnavailable(state.faceToObjectId.get(faceIndex));
    }}

    function visibleFaceIndices(faceIndices) {{
      return (faceIndices || []).filter(faceIndex => !isFaceHidden(faceIndex));
    }}

    function getHiddenComponentFaceSet() {{
      const hiddenFaces = new Set();
      for (const objectId of state.hiddenComponentObjectIds) {{
        const object = state.objectsById.get(objectId);
        if (!object || !object.face_indices) continue;
        for (const faceIndex of object.face_indices) {{
          hiddenFaces.add(faceIndex);
        }}
      }}
      return hiddenFaces;
    }}

    function getDeletedComponentFaceSet() {{
      const deletedFaces = new Set();
      for (const objectId of state.deletedComponentObjectIds) {{
        const object = state.objectsById.get(objectId);
        if (!object || !object.face_indices) continue;
        for (const faceIndex of object.face_indices) {{
          deletedFaces.add(faceIndex);
        }}
      }}
      return deletedFaces;
    }}

    function getViewerExcludedFaceSet() {{
      const excludedFaces = getHiddenComponentFaceSet();
      for (const faceIndex of getDeletedComponentFaceSet()) {{
        excludedFaces.add(faceIndex);
      }}
      return excludedFaces;
    }}

    function removeFacesFromSet(source, removedFaces) {{
      return new Set(Array.from(source || []).filter(faceIndex => !removedFaces.has(faceIndex)));
    }}

    function syncComponentVisibilityDom(objectId) {{
      if (!gapObjectList) return;
      const row = gapObjectList.querySelector('[data-component-row-id="' + objectId + '"]');
      if (!row) return;
      const hidden = isComponentHidden(objectId);
      const button = row.querySelector('[data-component-visibility]');
      row.classList.toggle('is-hidden', hidden);
      if (!button) return;
      button.textContent = hidden ? 'Show' : 'Hide';
      button.setAttribute('aria-pressed', hidden ? 'true' : 'false');
      button.setAttribute('aria-label', (hidden ? 'Show ' : 'Hide ') + (state.objectsById.get(objectId)?.object_name || ('component ' + objectId)) + ' in 3D viewer');
      button.title = hidden ? 'Show component in 3D viewer' : 'Hide component from 3D viewer';
    }}

    function syncComponentTraceabilityDom(objectId) {{
      if (!gapObjectList) return;
      const row = gapObjectList.querySelector('[data-component-row-id="' + objectId + '"]');
      if (!row) return;
      const traceable = isComponentTraceable(objectId);
      const button = row.querySelector('[data-component-traceability]');
      row.classList.toggle('is-non-traceable', !traceable);
      row.setAttribute('data-traceability', traceable ? 'on' : 'off');
      if (button) {{
        button.textContent = traceable ? 'Trace Off' : 'Trace On';
        button.setAttribute('aria-pressed', traceable ? 'true' : 'false');
        button.setAttribute(
          'aria-label',
          (traceable ? 'Disable' : 'Enable') + ' ray tracing collision for '
            + (state.objectsById.get(objectId)?.object_name || ('component ' + objectId))
        );
      }}
    }}

    function hideComponentContextMenu() {{
      componentContextMenu.classList.add('hidden-block');
      componentContextMenu.setAttribute('aria-hidden', 'true');
      state.componentContextMenuObjectId = null;
    }}

    function showComponentContextMenu(objectId, clientX, clientY, menuMode = 'full') {{
      const object = state.objectsById.get(objectId);
      if (!object || isComponentDeleted(objectId)) {{
        hideComponentContextMenu();
        return;
      }}
      state.componentContextMenuObjectId = objectId;
      state.componentContextMenuPosition = {{ clientX, clientY }};
      componentContextMenuName.textContent = object.object_name;
      const hidden = isComponentHidden(objectId);
      const traceable = isComponentTraceable(objectId);
      componentContextMenuStatus.textContent = (hidden ? 'Hidden' : 'Visible')
        + ' · ' + (traceable ? 'Traceability on' : 'Traceability off');
      componentContextVisibility.textContent = hidden ? 'Show' : 'Hide';
      componentContextTraceability.textContent = traceable ? 'Traceability Off' : 'Traceability On';
      const compactMenu = menuMode === 'compact';
      componentContextTraceability.classList.toggle('hidden-block', compactMenu);
      componentContextPrimarySeparator.classList.toggle('hidden-block', compactMenu);
      componentContextMaterial.classList.toggle('hidden-block', compactMenu);
      componentContextTransform.classList.toggle('hidden-block', compactMenu);
      componentContextMenu.classList.remove('hidden-block');
      componentContextMenu.setAttribute('aria-hidden', 'false');
      const menuWidth = componentContextMenu.offsetWidth || 230;
      const menuHeight = componentContextMenu.offsetHeight || 250;
      const left = Math.max(8, Math.min(window.innerWidth - menuWidth - 8, clientX));
      const top = Math.max(8, Math.min(window.innerHeight - menuHeight - 8, clientY));
      componentContextMenu.style.left = Math.round(left) + 'px';
      componentContextMenu.style.top = Math.round(top) + 'px';
      componentContextVisibility.focus({{ preventScroll: true }});
    }}

    function showComponentContextMenuForFace(faceIndex, clientX, clientY) {{
      const objectId = faceIndex === null || faceIndex === undefined
        ? null
        : state.faceToObjectId.get(faceIndex);
      if (objectId === null || objectId === undefined) {{
        hideComponentContextMenu();
        return;
      }}
      showComponentContextMenu(objectId, clientX, clientY);
    }}

    function toggleComponentVisibility(objectId) {{
      if (!state.objectsById.has(objectId) || isComponentDeleted(objectId)) return;
      if (isComponentHidden(objectId)) {{
        state.hiddenComponentObjectIds.delete(objectId);
      }} else {{
        state.hiddenComponentObjectIds.add(objectId);
      }}
      syncComponentVisibilityDom(objectId);
      drawViewer();
    }}

    function toggleComponentTraceability(objectId) {{
      if (!state.objectsById.has(objectId) || isComponentDeleted(objectId)) return;
      if (isComponentTraceable(objectId)) {{
        state.nonTraceableComponentObjectIds.add(objectId);
      }} else {{
        state.nonTraceableComponentObjectIds.delete(objectId);
      }}
      syncComponentTraceabilityDom(objectId);
      invalidateDirectRayTraceResult();
      updateRayTraceRunState();
      drawViewer();
    }}

    function runComponentContextMenuAction(action) {{
      const objectId = state.componentContextMenuObjectId;
      const popupPosition = state.componentContextMenuPosition
        ? {{ ...state.componentContextMenuPosition }}
        : null;
      hideComponentContextMenu();
      if (objectId === null || objectId === undefined || isComponentDeleted(objectId)) return;
      if (action === 'visibility') {{
        toggleComponentVisibility(objectId);
      }} else if (action === 'traceability') {{
        toggleComponentTraceability(objectId);
      }} else if (action === 'material') {{
        focusMaterialForObject(objectId, popupPosition);
      }} else if (action === 'transform') {{
        startTransformForObject(objectId, popupPosition);
      }} else if (action === 'delete') {{
        deleteComponentObject(objectId);
      }}
    }}

    function deleteComponentObject(objectId) {{
      const object = state.objectsById.get(objectId);
      if (!object || isComponentDeleted(objectId)) return;
      const confirmed = window.confirm(
        '“' + object.object_name + '” component를 삭제할까요?\\n\\n'
        + '3D viewer와 ray tracing 해석 대상에서 제거되며, 연결된 Transform / Material / Face emitter 설정도 함께 정리됩니다.\\n'
        + '되돌리려면 CAD를 다시 불러와야 합니다.'
      );
      if (!confirmed) return;

      const deletedFaces = new Set(object.face_indices || []);
      state.deletedComponentObjectIds.add(objectId);
      state.hiddenComponentObjectIds.delete(objectId);
      state.nonTraceableComponentObjectIds.delete(objectId);
      state.selectedObjectIds.delete(objectId);
      state.selectedGapObjectIds.delete(objectId);
      const remainingGapIds = uniqueSorted(Array.from(state.selectedGapObjectIds).filter(id => !isComponentDeleted(id)));
      state.selectedGapObjectIds = new Set(remainingGapIds);
      state.selectedGapObjectId = remainingGapIds.length ? remainingGapIds[0] : null;
      state.clickedFaces = removeFacesFromSet(state.clickedFaces, deletedFaces);
      state.panelFaces = removeFacesFromSet(state.panelFaces, deletedFaces);
      state.selectedFaces = removeFacesFromSet(state.selectedFaces, deletedFaces);
      state.localGapFaces = removeFacesFromSet(state.localGapFaces, deletedFaces);
      state.selectedGapFaces = removeFacesFromSet(state.selectedGapFaces, deletedFaces);
      state.emitterDraftFaces = removeFacesFromSet(state.emitterDraftFaces, deletedFaces);
      if (state.inspectedFaceIndex !== null && deletedFaces.has(state.inspectedFaceIndex)) {{
        state.inspectedFaceIndex = null;
      }}
      roiInput.value = parseFaceList(roiInput.value).filter(faceIndex => !deletedFaces.has(faceIndex)).join(',');
      gapFaceInput.value = parseFaceList(gapFaceInput.value).filter(faceIndex => !deletedFaces.has(faceIndex)).join(',');

      const removedRuleIds = new Set(
        state.transformRules.filter(rule => rule.object_id === objectId).map(rule => rule.rule_id)
      );
      state.transformRules = state.transformRules.filter(rule => rule.object_id !== objectId);
      state.selectedTransformRuleIds = new Set(
        Array.from(state.selectedTransformRuleIds).filter(ruleId => !removedRuleIds.has(ruleId))
      );
      if (removedRuleIds.has(state.activeTransformRuleId)) {{
        state.activeTransformRuleId = state.transformRules.length ? state.transformRules[0].rule_id : null;
      }}
      if (state.activeTransformRuleId) {{
        syncEditorFromActiveRule();
      }} else {{
        state.gapMove = {{ x: 0, y: 0, z: 0 }};
        state.gapMoveText = {{ x: '0', y: '0', z: '0' }};
        state.gapTilt = {{ x: 0, y: 0, z: 0 }};
        state.gapTiltText = {{ x: '0', y: '0', z: '0' }};
        syncTransformInputs();
        hideMovePopup();
      }}

      state.materialAssignments = state.materialAssignments.filter(item => item.object_id !== objectId);
      if (state.selectedMaterialObjectId === objectId) {{
        state.selectedMaterialObjectId = null;
        hideMaterialPopup();
      }}

      const removedEmitterIds = new Set();
      state.emitters = state.emitters.filter(emitter => {{
        if ((emitter.emitter_type || 'face') !== 'face') return true;
        emitter.face_indices = (emitter.face_indices || []).filter(faceIndex => !deletedFaces.has(faceIndex));
        if (emitter.face_indices.length) return true;
        removedEmitterIds.add(emitter.emitter_id);
        return false;
      }});
      if (removedEmitterIds.has(state.activeEmitterId)) {{
        state.activeEmitterId = null;
        state.emitterSelectionActive = false;
        hideEmitterPopup();
      }}

      gapObjectList.querySelector('[data-component-row-id="' + objectId + '"]')?.remove();
      objectList.querySelector('[data-roi-object-row-id="' + objectId + '"]')?.remove();
      if (!gapObjectList.querySelector('[data-component-row-id]')) {{
        gapObjectList.innerHTML = '<div class="small">모든 component가 삭제되었습니다. 복원하려면 CAD를 다시 불러오세요.</div>';
      }}
      if (!objectList.querySelector('[data-roi-object-row-id]')) {{
        objectList.innerHTML = '<div class="small">ROI로 선택할 component가 없습니다.</div>';
      }}

      const remainingFaceCount = Math.max(0, (state.mesh?.faces?.length || 0) - getDeletedComponentFaceSet().size);
      kpiFaces.textContent = String(remainingFaceCount);
      invalidateDirectRayTraceResult();
      renderTransformRules();
      renderMaterialLibrary();
      renderEmitterList();
      updateMaterialTargetSummary();
      updateGapSelectionStats();
      recomputeSelectedFaces();
      updateRayTraceRunState();
    }}

    function refreshComponentNameDom(objectId) {{
      const item = state.objectsById.get(objectId);
      if (!item) return;
      const componentRow = gapObjectList ? gapObjectList.querySelector('[data-component-row-id=\"' + objectId + '\"]') : null;
      if (componentRow) {{
        const nameEl = componentRow.querySelector('[data-component-name]');
        if (nameEl && nameEl.tagName !== 'INPUT') {{
          nameEl.textContent = item.object_name;
          nameEl.title = item.object_name + ' · Double-click or press F2 to rename';
        }}
        const materialButton = componentRow.querySelector('[data-component-material]');
        if (materialButton) materialButton.setAttribute('aria-label', 'Material for ' + item.object_name);
        const transformButton = componentRow.querySelector('[data-component-transform]');
        if (transformButton) transformButton.setAttribute('aria-label', 'Transform ' + item.object_name);
        const moreButton = componentRow.querySelector('[data-component-more]');
        if (moreButton) moreButton.setAttribute('aria-label', 'Hide, show, or delete ' + item.object_name);
        syncComponentVisibilityDom(objectId);
        syncComponentTraceabilityDom(objectId);
      }}
      const roiRow = objectList ? objectList.querySelector('[data-roi-object-row-id=\"' + objectId + '\"]') : null;
      if (roiRow) {{
        const label = roiRow.querySelector('.roi-object-label');
        if (label) label.textContent = objectLabel(item);
      }}
    }}

    function renameComponentObject(objectId, nextName) {{
      const item = state.objectsById.get(objectId);
      if (!item) return false;
      const trimmed = String(nextName ?? '').trim();
      if (!trimmed) return false;
      item.object_name = trimmed;
      item.component_name = trimmed;
      const rule = getTransformRuleByObjectId(objectId);
      if (rule) rule.label = trimmed;
      for (const assignment of state.materialAssignments) {{
        if (assignment.target_type === 'part' && assignment.object_id === objectId) {{
          assignment.target_name = trimmed;
        }}
      }}
      refreshComponentNameDom(objectId);
      syncComponentSelectionSummary();
      renderTransformRules();
      renderMaterialLibrary();
      updateMaterialTargetSummary();
      updateGapSelectionStats();
      drawViewer();
      return true;
    }}

    function beginRenameComponent(objectId) {{
      const item = state.objectsById.get(objectId);
      if (!item || !gapObjectList) return;
      const row = gapObjectList.querySelector('[data-component-row-id=\"' + objectId + '\"]');
      if (!row) return;
      const nameEl = row.querySelector('[data-component-name]');
      if (!nameEl || nameEl.tagName === 'INPUT') return;
      const input = document.createElement('input');
      input.type = 'text';
      input.className = 'component-name-input';
      input.value = item.object_name;
      input.setAttribute('aria-label', 'Component name');
      nameEl.replaceWith(input);
      input.focus();
      input.select();
      let finished = false;
      const finish = (save) => {{
        if (finished) return;
        finished = true;
        const value = input.value;
        const next = document.createElement('div');
        next.className = 'name';
        next.setAttribute('data-component-name', String(objectId));
        next.setAttribute('tabindex', '0');
        next.title = 'Double-click or press F2 to rename';
        input.replaceWith(next);
        if (save) {{
          renameComponentObject(objectId, value);
        }} else {{
          refreshComponentNameDom(objectId);
        }}
      }};
      input.addEventListener('keydown', function (ev) {{
        if (ev.key === 'Enter') {{
          ev.preventDefault();
          ev.stopPropagation();
          finish(true);
        }} else if (ev.key === 'Escape') {{
          ev.preventDefault();
          ev.stopPropagation();
          finish(false);
        }}
      }});
      input.addEventListener('blur', function () {{
        finish(true);
      }});
    }}

    function getTransformRuleById(ruleId) {{
      return state.transformRules.find(rule => rule.rule_id === ruleId) || null;
    }}

    function getTransformRuleByObjectId(objectId) {{
      return state.transformRules.find(rule => rule.target_type === 'component' && rule.object_id === objectId) || null;
    }}

    function selectedComponentObjectIds() {{
      return uniqueSorted(Array.from(state.selectedGapObjectIds));
    }}

    function selectedTransformRuleIds() {{
      return uniqueSorted(Array.from(state.selectedTransformRuleIds));
    }}

    function buildTransformRule(objectId) {{
      const item = state.objectsById.get(objectId);
      if (!item || isComponentDeleted(objectId)) return null;
      return {{
        rule_id: 'tr_' + objectId + '_' + Date.now() + '_' + Math.floor(Math.random() * 1000),
        target_type: 'component',
        object_id: objectId,
        label: item.object_name,
        enabled: true,
        move: {{ x: 0, y: 0, z: 0 }},
        tilt: {{ x: 0, y: 0, z: 0 }}
      }};
    }}

    function ensureTransformRuleForObject(objectId) {{
      let rule = getTransformRuleByObjectId(objectId);
      if (!rule) {{
        rule = buildTransformRule(objectId);
        if (!rule) return null;
        state.transformRules.push(rule);
      }}
      state.activeTransformRuleId = rule.rule_id;
      syncEditorFromActiveRule();
      return rule;
    }}

    function activeTransformRule() {{
      return state.activeTransformRuleId ? getTransformRuleById(state.activeTransformRuleId) : null;
    }}

    function ensureActiveTransformRule() {{
      if (activeTransformRule()) return;
      if (state.transformRules.length) {{
        state.activeTransformRuleId = state.transformRules[0].rule_id;
      }}
    }}

    function syncComponentSelectionSummary() {{
      const ids = selectedComponentObjectIds();
      if (!ids.length) {{
        componentSelectionSummary.textContent = '선택된 부품 없음. Component row 또는 3D 형상을 클릭하면 선택되며, Transform 버튼이나 우클릭 메뉴로 입력창을 엽니다.';
        return;
      }}
      const names = ids.slice(0, 4).map(id => {{
        const item = state.objectsById.get(id);
        return item ? item.object_name : ('Part ' + id);
      }});
      const suffix = ids.length > 4 ? ' 외 ' + (ids.length - 4) + '개' : '';
      componentSelectionSummary.textContent = '선택 부품 ' + ids.length + '개\\n' + names.join(', ') + suffix;
    }}

    function buildInitialMaterialBaseLibrary() {{
      return [
        {{ material_id: 'black_powder_coated_aluminum', name: 'Black powder coated aluminum', category: 'metal', reflectance_total: 0.12, default_surface_id: 'black_powder_coat_fine' }},
        {{ material_id: 'black_pc_resin', name: 'Black PC resin', category: 'resin', reflectance_total: 0.08, default_surface_id: 'matte_black_resin' }},
        {{ material_id: 'anodized_aluminum', name: 'Anodized aluminum', category: 'metal', reflectance_total: 0.18, default_surface_id: 'anodized_matte' }},
        {{ material_id: 'matte_black_abs', name: 'Matte black ABS', category: 'resin', reflectance_total: 0.08, default_surface_id: 'matte_black_resin' }},
        {{ material_id: 'black_tape_general', name: 'Black tape', category: 'tape', reflectance_total: 0.05, default_surface_id: 'tape_black_matte' }},
        {{ material_id: 'foam_absorber_general', name: 'Foam absorber', category: 'foam', reflectance_total: 0.03, default_surface_id: 'foam_low_reflect' }}
      ];
    }}

    function buildInitialMaterialSurfaceLibrary() {{
      return [
        {{ surface_id: 'black_powder_coat_fine', name: 'Black powder coat - fine', scatter_model: 'gaussian', reflectance_scale: 1.00, specular_ratio: 0.15, diffuse_ratio: 0.85, roughness: 0.70, scatter_sigma_deg: 18 }},
        {{ surface_id: 'black_powder_coat_coarse', name: 'Black powder coat - coarse', scatter_model: 'gaussian', reflectance_scale: 1.33, specular_ratio: 0.05, diffuse_ratio: 0.95, roughness: 0.82, scatter_sigma_deg: 28 }},
        {{ surface_id: 'matte_black_resin', name: 'Matte black resin', scatter_model: 'lambertian', reflectance_scale: 1.00, specular_ratio: 0.0, diffuse_ratio: 1.0, roughness: 0.88, scatter_sigma_deg: 32 }},
        {{ surface_id: 'semi_gloss_black_resin', name: 'Semi-gloss black resin', scatter_model: 'mixed', reflectance_scale: 1.25, specular_ratio: 0.40, diffuse_ratio: 0.60, roughness: 0.45, scatter_sigma_deg: 14 }},
        {{ surface_id: 'anodized_matte', name: 'Anodized matte', scatter_model: 'mixed', reflectance_scale: 1.00, specular_ratio: 0.45, diffuse_ratio: 0.55, roughness: 0.50, scatter_sigma_deg: 12 }},
        {{ surface_id: 'tape_black_matte', name: 'Black tape matte', scatter_model: 'lambertian', reflectance_scale: 1.00, specular_ratio: 0.0, diffuse_ratio: 1.0, roughness: 0.92, scatter_sigma_deg: 38 }},
        {{ surface_id: 'foam_low_reflect', name: 'Foam low reflect', scatter_model: 'lambertian', reflectance_scale: 1.00, specular_ratio: 0.0, diffuse_ratio: 1.0, roughness: 0.98, scatter_sigma_deg: 45 }},
        {{ surface_id: 'corrosion_light', name: 'Corrosion - light', scatter_model: 'gaussian', reflectance_scale: 1.17, specular_ratio: 0.10, diffuse_ratio: 0.90, roughness: 0.76, scatter_sigma_deg: 24 }},
        {{ surface_id: 'corrosion_medium', name: 'Corrosion - medium', scatter_model: 'gaussian', reflectance_scale: 1.50, specular_ratio: 0.05, diffuse_ratio: 0.95, roughness: 0.84, scatter_sigma_deg: 34 }},
        {{ surface_id: 'corrosion_heavy', name: 'Corrosion - heavy', scatter_model: 'gaussian', reflectance_scale: 1.83, specular_ratio: 0.02, diffuse_ratio: 0.98, roughness: 0.94, scatter_sigma_deg: 46 }}
      ];
    }}

    function buildInitialMaterialProfiles() {{
      return [
        {{ profile_id: 'profile_tv_black_default', name: 'TV black default', base_material_id: 'black_pc_resin', surface_id: 'matte_black_resin', bsdf_asset_id: '' }},
        {{ profile_id: 'profile_black_chassis_default', name: 'Black chassis default', base_material_id: 'black_powder_coated_aluminum', surface_id: 'black_powder_coat_fine', bsdf_asset_id: '' }},
        {{ profile_id: 'profile_corrosion_medium', name: 'Corrosion medium edge', base_material_id: 'black_powder_coated_aluminum', surface_id: 'corrosion_medium', bsdf_asset_id: '' }}
      ];
    }}

    function ensureMaterialLibraryState() {{
      if (!state.materialBaseLibrary.length) {{
        state.materialBaseLibrary = buildInitialMaterialBaseLibrary();
      }}
      if (!state.materialSurfaceLibrary.length) {{
        state.materialSurfaceLibrary = buildInitialMaterialSurfaceLibrary();
      }}
      if (!state.materialOpticalProfiles.length) {{
        state.materialOpticalProfiles = buildInitialMaterialProfiles();
      }}
    }}

    function getMaterialBaseById(materialId) {{
      ensureMaterialLibraryState();
      return state.materialBaseLibrary.find(item => item.material_id === materialId) || state.materialBaseLibrary[0] || null;
    }}

    function getMaterialSurfaceById(surfaceId) {{
      ensureMaterialLibraryState();
      return state.materialSurfaceLibrary.find(item => item.surface_id === surfaceId) || state.materialSurfaceLibrary[0] || null;
    }}

    function getMaterialProfileById(profileId) {{
      ensureMaterialLibraryState();
      return state.materialOpticalProfiles.find(item => item.profile_id === profileId) || null;
    }}

    function normalizedOpticalRatios(surface) {{
      let specular = Math.max(0, Number(surface?.specular_ratio) || 0);
      let diffuse = Math.max(0, Number(surface?.diffuse_ratio) || 0);
      if (specular + diffuse <= 0) {{
        const model = String(surface?.scatter_model || 'lambertian');
        if (model === 'specular') {{ specular = 1; diffuse = 0; }}
        else {{ specular = 0; diffuse = 1; }}
      }}
      const total = specular + diffuse;
      return {{ specular: specular / total, diffuse: diffuse / total }};
    }}

    function compileRayTraceOpticalProfile(profileId, baseId, surfaceId, notes) {{
      ensureMaterialLibraryState();
      const base = state.materialBaseLibrary.find(item => item.material_id === baseId) || null;
      const surface = state.materialSurfaceLibrary.find(item => item.surface_id === surfaceId) || null;
      if (!base || !surface) return null;
      const baseReflectance = Number(base.reflectance_total);
      const rawReflectanceScale = Number(surface.reflectance_scale);
      const reflectanceScale = Math.max(0, Number.isFinite(rawReflectanceScale) ? rawReflectanceScale : 1);
      const reflectance = Math.max(0, Math.min(1,
        (Number.isFinite(baseReflectance) ? baseReflectance : 0) * reflectanceScale
      ));
      const ratios = normalizedOpticalRatios(surface);
      return {{
        profile_id: profileId,
        reflectance: reflectance,
        absorption: 1 - reflectance,
        specular_ratio: ratios.specular,
        diffuse_ratio: ratios.diffuse,
        scatter_model: String(surface.scatter_model || 'lambertian'),
        roughness: Math.max(0, Math.min(1, Number(surface.roughness) || 0)),
        gaussian_sigma_deg: Math.max(0.001, Number(surface.scatter_sigma_deg) || 18),
        bsdf_asset_id: '',
        notes: notes || (base.name + ' / ' + surface.name)
      }};
    }}

    function buildRayTraceOpticalPayload() {{
      ensureMaterialLibraryState();
      const profilesById = new Map();
      for (const base of state.materialBaseLibrary) {{
        const profile = compileRayTraceOpticalProfile(
          base.material_id,
          base.material_id,
          base.default_surface_id,
          'Mesh material fallback: ' + base.name
        );
        if (profile) profilesById.set(profile.profile_id, profile);
      }}
      for (const saved of state.materialOpticalProfiles) {{
        const profile = compileRayTraceOpticalProfile(
          saved.profile_id,
          saved.base_material_id,
          saved.surface_id,
          'Saved optical profile: ' + saved.name
        );
        if (profile) {{
          profile.bsdf_asset_id = saved.bsdf_asset_id || '';
          profilesById.set(profile.profile_id, profile);
        }}
      }}
      const assignments = [];
      state.materialAssignments.forEach((assignment, index) => {{
        let profileId = assignment.profile_id || ('assignment_profile_' + assignment.assignment_id);
        if (!profilesById.has(profileId)) {{
          const profile = compileRayTraceOpticalProfile(
            profileId,
            assignment.base_material_id,
            assignment.surface_id,
            'Assignment: ' + assignment.target_name
          );
          if (profile) {{
            profile.bsdf_asset_id = assignment.bsdf_asset_id || '';
            profilesById.set(profile.profile_id, profile);
          }}
        }}
        if (!profilesById.has(profileId)) return;
        assignments.push({{
          assignment_id: assignment.assignment_id,
          target_type: assignment.target_type,
          component_id: assignment.object_id,
          profile_id: profileId,
          face_indices: assignment.face_indices || [],
          priority: index,
          enabled: true
        }});
      }});
      return {{
        profiles: Array.from(profilesById.values()),
        assignments: assignments
      }};
    }}

    function currentMaterialObject() {{
      if (state.selectedMaterialObjectId === null || state.selectedMaterialObjectId === undefined) return null;
      return state.objectsById.get(state.selectedMaterialObjectId) || null;
    }}

    function selectedMaterialFaceIndices() {{
      return uniqueSorted(Array.from(state.selectedFaces));
    }}

    function currentMaterialTargetLabel() {{
      const object = currentMaterialObject();
      if (!object) return '선택된 material 대상 부품 없음';
      if (state.materialTargetMode === 'faces') {{
        const faces = selectedMaterialFaceIndices();
        return object.object_name + ' / selected faces: ' + faces.length;
      }}
      return object.object_name;
    }}

    function findMaterialAssignment(targetType, objectId, faceSignature) {{
      return state.materialAssignments.find(item =>
        item.target_type === targetType
        && item.object_id === objectId
        && (targetType !== 'faces' || item.face_signature === faceSignature)
      ) || null;
    }}

    function defaultSurfaceIdForBase(baseId) {{
      const base = getMaterialBaseById(baseId);
      return base && base.default_surface_id ? base.default_surface_id : 'matte_black_resin';
    }}

    function syncMaterialPopupSelects() {{
      ensureMaterialLibraryState();
      if (newMaterialDefaultSurface) {{
        newMaterialDefaultSurface.innerHTML = state.materialSurfaceLibrary
          .map(item => '<option value=\"' + item.surface_id + '\">' + item.name + '</option>')
          .join('');
      }}
      if (materialPopupBaseSelect) {{
        materialPopupBaseSelect.innerHTML = state.materialBaseLibrary
          .map(item => '<option value=\"' + item.material_id + '\">' + item.name + '</option>')
          .join('');
        materialPopupBaseSelect.value = state.materialDraft.base_material_id;
      }}
      if (materialPopupSurfaceSelect) {{
        materialPopupSurfaceSelect.innerHTML = state.materialSurfaceLibrary
          .map(item => '<option value=\"' + item.surface_id + '\">' + item.name + '</option>')
          .join('');
        materialPopupSurfaceSelect.value = state.materialDraft.surface_id;
      }}
      if (materialPopupProfileSelect) {{
        materialPopupProfileSelect.innerHTML = '<option value=\"\">None (draft only)</option>'
          + state.materialOpticalProfiles
            .map(item => '<option value=\"' + item.profile_id + '\">' + item.name + '</option>')
            .join('');
        materialPopupProfileSelect.value = state.materialDraft.profile_id || '';
      }}
      if (materialTargetMode) {{
        materialTargetMode.value = state.materialTargetMode;
      }}
    }}

    function syncMaterialDraftFromObject(objectId) {{
      ensureMaterialLibraryState();
      const assignment = findMaterialAssignment('part', objectId, '');
      if (assignment) {{
        state.materialDraft.base_material_id = assignment.base_material_id;
        state.materialDraft.surface_id = assignment.surface_id;
        state.materialDraft.profile_id = assignment.profile_id || '';
        state.materialDraft.bsdf_asset_id = assignment.bsdf_asset_id || '';
      }} else {{
        const fallbackBase = 'black_pc_resin';
        state.materialDraft.base_material_id = fallbackBase;
        state.materialDraft.surface_id = defaultSurfaceIdForBase(fallbackBase);
        state.materialDraft.profile_id = 'profile_tv_black_default';
        state.materialDraft.bsdf_asset_id = '';
      }}
      syncMaterialPopupSelects();
    }}

    function hideMaterialLibraryForms() {{
      if (newMaterialForm) newMaterialForm.classList.add('hidden-block');
      if (newSurfaceForm) newSurfaceForm.classList.add('hidden-block');
      if (newBsdfForm) newBsdfForm.classList.add('hidden-block');
    }}

    function showMaterialLibraryForm(formName) {{
      hideMaterialLibraryForms();
      if (formName === 'material' && newMaterialForm) {{
        newMaterialForm.classList.remove('hidden-block');
      }} else if (formName === 'surface' && newSurfaceForm) {{
        newSurfaceForm.classList.remove('hidden-block');
      }} else if (formName === 'bsdf' && newBsdfForm) {{
        newBsdfForm.classList.remove('hidden-block');
      }}
    }}

    function currentMaterialPopupSummaryText() {{
      const object = currentMaterialObject();
      if (!object) return '선택된 material 대상 없음';
      const base = getMaterialBaseById(state.materialDraft.base_material_id);
      const surface = getMaterialSurfaceById(state.materialDraft.surface_id);
      const profile = getMaterialProfileById(state.materialDraft.profile_id);
      const faces = selectedMaterialFaceIndices();
      const assignmentCount = state.materialAssignments.filter(item => item.object_id === object.object_id).length;
      return 'Target: ' + object.object_name + '\\n'
        + 'Apply mode: ' + (state.materialTargetMode === 'faces' ? 'Face override' : 'Part assignment') + '\\n'
        + 'Base material: ' + (base ? base.name : '-') + '\\n'
        + 'Surface finish: ' + (surface ? surface.name : '-') + '\\n'
        + 'Saved profile: ' + (profile ? profile.name : 'None (draft only)') + '\\n'
        + 'Selected faces: ' + faces.length + '\\n'
        + 'Assignments on this part: ' + assignmentCount;
    }}

    function renderMaterialLibrary() {{
      ensureMaterialLibraryState();
      if (materialBaseList) {{
        materialBaseList.innerHTML = state.materialBaseLibrary.map(item =>
          '<div class=\"library-row' + (item.material_id === state.materialDraft.base_material_id ? ' active' : '') + '\">'
          + '<div class=\"name\">' + item.name + '</div>'
          + '<div class=\"meta\">category: ' + item.category + '\\nreflectance: ' + Number(item.reflectance_total || 0).toFixed(3) + '\\ndefault surface: ' + item.default_surface_id + '</div>'
          + '<div class=\"library-actions-inline\"><button type=\"button\" data-material-base=\"' + item.material_id + '\">Use in popup</button></div>'
          + '</div>'
        ).join('');
        const buttons = materialBaseList.querySelectorAll('[data-material-base]');
        for (const button of buttons) {{
          button.addEventListener('click', function (ev) {{
            const baseId = ev.currentTarget.getAttribute('data-material-base');
            state.materialDraft.base_material_id = baseId;
            state.materialDraft.surface_id = defaultSurfaceIdForBase(baseId);
            state.materialDraft.profile_id = '';
            syncMaterialPopupSelects();
            updateMaterialTargetSummary();
            renderMaterialLibrary();
          }});
        }}
      }}
      if (materialSurfaceList) {{
        materialSurfaceList.innerHTML = state.materialSurfaceLibrary.map(item =>
          '<div class=\"library-row' + (item.surface_id === state.materialDraft.surface_id ? ' active' : '') + '\">'
          + '<div class=\"name\">' + item.name + '</div>'
          + '<div class=\"meta\">scatter: ' + item.scatter_model + '\\nR multiplier: ' + Number(Number.isFinite(Number(item.reflectance_scale)) ? item.reflectance_scale : 1).toFixed(2) + ' / roughness: ' + Number(item.roughness).toFixed(2) + '</div>'
          + '<div class=\"library-actions-inline\"><button type=\"button\" data-material-surface=\"' + item.surface_id + '\">Use in popup</button></div>'
          + '</div>'
        ).join('');
        const buttons = materialSurfaceList.querySelectorAll('[data-material-surface]');
        for (const button of buttons) {{
          button.addEventListener('click', function (ev) {{
            const surfaceId = ev.currentTarget.getAttribute('data-material-surface');
            state.materialDraft.surface_id = surfaceId;
            state.materialDraft.profile_id = '';
            syncMaterialPopupSelects();
            updateMaterialTargetSummary();
            renderMaterialLibrary();
          }});
        }}
      }}
      if (materialProfileList) {{
        materialProfileList.innerHTML = state.materialOpticalProfiles.map(item => {{
          const base = getMaterialBaseById(item.base_material_id);
          const surface = getMaterialSurfaceById(item.surface_id);
          return '<div class=\"library-row' + (item.profile_id === state.materialDraft.profile_id ? ' active' : '') + '\">'
            + '<div class=\"name\">' + item.name + '</div>'
            + '<div class=\"meta\">base: ' + (base ? base.name : item.base_material_id) + '\\nsurface: ' + (surface ? surface.name : item.surface_id) + (item.bsdf_asset_id ? ('\\nbsdf: ' + item.bsdf_asset_id) : '') + '</div>'
            + '<div class=\"library-actions-inline\"><button type=\"button\" data-material-profile=\"' + item.profile_id + '\">Use in popup</button></div>'
            + '</div>';
        }}).join('');
        const buttons = materialProfileList.querySelectorAll('[data-material-profile]');
        for (const button of buttons) {{
          button.addEventListener('click', function (ev) {{
            const profile = getMaterialProfileById(ev.currentTarget.getAttribute('data-material-profile'));
            if (!profile) return;
            state.materialDraft.base_material_id = profile.base_material_id;
            state.materialDraft.surface_id = profile.surface_id;
            state.materialDraft.profile_id = profile.profile_id;
            state.materialDraft.bsdf_asset_id = profile.bsdf_asset_id || '';
            syncMaterialPopupSelects();
            updateMaterialTargetSummary();
            renderMaterialLibrary();
          }});
        }}
      }}
      if (bsdfAssetList) {{
        bsdfAssetList.innerHTML = state.bsdfAssets.length
          ? state.bsdfAssets.map(item =>
              '<div class=\"library-row' + (item.bsdf_asset_id === state.materialDraft.bsdf_asset_id ? ' active' : '') + '\">'
              + '<div class=\"name\">' + item.name + '</div>'
              + '<div class=\"meta\">status: ' + item.status + '\\nfile: ' + item.file_name + '</div>'
              + '<div class=\"library-actions-inline\"><button type=\"button\" data-bsdf-asset=\"' + item.bsdf_asset_id + '\">Link to popup</button></div>'
              + '</div>'
            ).join('')
          : '<div class=\"assignment-empty\">등록된 BSDF asset이 없습니다.</div>';
        const buttons = bsdfAssetList.querySelectorAll('[data-bsdf-asset]');
        for (const button of buttons) {{
          button.addEventListener('click', function (ev) {{
            state.materialDraft.bsdf_asset_id = ev.currentTarget.getAttribute('data-bsdf-asset');
            updateMaterialTargetSummary();
            renderMaterialLibrary();
          }});
        }}
      }}
      if (materialAssignmentList && materialAssignmentEmpty) {{
        materialAssignmentList.innerHTML = state.materialAssignments.map(item => {{
          const typeLabel = item.target_type === 'faces' ? 'Face override' : 'Part assignment';
          return '<div class=\"library-row\">'
            + '<div class=\"name\">' + item.target_name + '</div>'
            + '<div class=\"meta\">type: ' + typeLabel + '\\nbase: ' + item.base_material_id + '\\nsurface: ' + item.surface_id + (item.face_count ? ('\\nfaces: ' + item.face_count) : '') + '</div>'
            + '<div class=\"library-actions-inline\"><button type=\"button\" data-material-jump=\"' + item.object_id + '\">Jump to target</button></div>'
            + '</div>';
        }}).join('');
        materialAssignmentEmpty.classList.toggle('hidden-block', state.materialAssignments.length > 0);
        const buttons = materialAssignmentList.querySelectorAll('[data-material-jump]');
        for (const button of buttons) {{
          button.addEventListener('click', function (ev) {{
            const objectId = parseInt(ev.currentTarget.getAttribute('data-material-jump'), 10);
            focusMaterialForObject(objectId);
          }});
        }}
      }}
      syncMaterialPopupSelects();
    }}

    function updateMaterialTargetSummary() {{
      const object = currentMaterialObject();
      if (!materialTargetSummary) return;
      if (!object) {{
        materialTargetSummary.textContent = '선택된 material 대상 부품 없음\\nComponents의 Material 버튼을 누르거나 3D viewer popup에서 대상을 잡아주세요.';
        if (cursorMaterialChip) cursorMaterialChip.textContent = 'No target';
        if (cursorMaterialName) cursorMaterialName.textContent = '선택된 material 대상 없음';
        if (cursorMaterialSummary) cursorMaterialSummary.textContent = '선택된 material 대상 없음';
        return;
      }}
      const faceCount = selectedMaterialFaceIndices().length;
      const assignmentCount = state.materialAssignments.filter(item => item.object_id === object.object_id).length;
      materialTargetSummary.textContent = '선택 대상: ' + object.object_name + '\\n'
        + 'viewer popup에서 Part assignment / Face override를 적용하세요.\\n'
        + '선택 faces: ' + faceCount + ' / assignments: ' + assignmentCount;
      if (cursorMaterialChip) {{
        cursorMaterialChip.textContent = object.object_name;
      }}
      if (cursorMaterialName) {{
        cursorMaterialName.textContent = object.object_name + ' / popup에서 material을 지정 후 Apply';
      }}
      if (cursorMaterialSummary) {{
        cursorMaterialSummary.textContent = currentMaterialPopupSummaryText();
      }}
    }}

    function applyMaterialAssignment(targetModeOverride) {{
      const object = currentMaterialObject();
      if (!object) {{
        updateMaterialTargetSummary();
        return;
      }}
      const targetType = targetModeOverride || state.materialTargetMode || 'part';
      const faceIndices = targetType === 'faces' ? selectedMaterialFaceIndices() : [];
      if (targetType === 'faces' && !faceIndices.length) {{
        if (cursorMaterialSummary) {{
          cursorMaterialSummary.textContent = currentMaterialPopupSummaryText() + '\\n경고: Face override를 적용하려면 먼저 ROI face를 선택해야 합니다.';
        }}
        return;
      }}
      const faceSignature = faceIndices.join(',');
      const existing = findMaterialAssignment(targetType, object.object_id, faceSignature);
      const assignment = {{
        assignment_id: existing ? existing.assignment_id : ('mat_' + object.object_id + '_' + Date.now()),
        object_id: object.object_id,
        target_type: targetType,
        target_name: object.object_name,
        face_indices: faceIndices,
        face_signature: faceSignature,
        face_count: faceIndices.length,
        base_material_id: state.materialDraft.base_material_id,
        surface_id: state.materialDraft.surface_id,
        profile_id: state.materialDraft.profile_id || '',
        bsdf_asset_id: state.materialDraft.bsdf_asset_id || ''
      }};
      if (existing) {{
        Object.assign(existing, assignment);
      }} else {{
        state.materialAssignments.push(assignment);
      }}
      invalidateDirectRayTraceResult();
      renderMaterialLibrary();
      updateMaterialTargetSummary();
      drawViewer();
    }}

    function saveCurrentMaterialProfile() {{
      ensureMaterialLibraryState();
      const base = getMaterialBaseById(state.materialDraft.base_material_id);
      const surface = getMaterialSurfaceById(state.materialDraft.surface_id);
      if (!base || !surface) return;
      const profileId = 'profile_' + Date.now();
      const profile = {{
        profile_id: profileId,
        name: base.name + ' / ' + surface.name,
        base_material_id: base.material_id,
        surface_id: surface.surface_id,
        bsdf_asset_id: state.materialDraft.bsdf_asset_id || ''
      }};
      state.materialOpticalProfiles.unshift(profile);
      state.materialDraft.profile_id = profileId;
      renderMaterialLibrary();
      updateMaterialTargetSummary();
    }}

    function registerCustomMaterial() {{
      ensureMaterialLibraryState();
      const name = String(newMaterialName.value || '').trim();
      if (!name) return;
      const category = String(newMaterialCategory.value || 'resin').trim();
      const reflectance = Math.max(0, Math.min(1, Number(newMaterialReflectance.value) || 0));
      const defaultSurfaceId = String(newMaterialDefaultSurface.value || '').trim() || 'matte_black_resin';
      const materialId = 'material_' + name.toLowerCase().replace(/[^a-z0-9]+/g, '_') + '_' + Date.now();
      state.materialBaseLibrary.unshift({{
        material_id: materialId,
        name: name,
        category: category,
        reflectance_total: reflectance,
        default_surface_id: defaultSurfaceId
      }});
      state.materialDraft.base_material_id = materialId;
      state.materialDraft.surface_id = defaultSurfaceId;
      state.materialDraft.profile_id = '';
      newMaterialName.value = '';
      if (newMaterialReflectance) newMaterialReflectance.value = '0.08';
      if (newMaterialCategory) newMaterialCategory.value = 'resin';
      hideMaterialLibraryForms();
      renderMaterialLibrary();
      updateMaterialTargetSummary();
    }}

    function registerCustomSurface() {{
      ensureMaterialLibraryState();
      const name = String(customSurfaceName.value || '').trim();
      if (!name) return;
      const surface = {{
        surface_id: 'surface_' + name.toLowerCase().replace(/[^a-z0-9]+/g, '_') + '_' + Date.now(),
        name: name,
        scatter_model: customSurfaceScatter.value || 'gaussian',
        reflectance_scale: parseFloat(customSurfaceReflectance.value || '1') || 1,
        specular_ratio: parseFloat(customSurfaceSpecular.value || '0') || 0,
        diffuse_ratio: parseFloat(customSurfaceDiffuse.value || '0') || 0,
        roughness: parseFloat(customSurfaceRoughness.value || '0') || 0,
        scatter_sigma_deg: parseFloat(customSurfaceScatterWidth.value || '0') || 0
      }};
      state.materialSurfaceLibrary.unshift(surface);
      state.materialDraft.surface_id = surface.surface_id;
      state.materialDraft.profile_id = '';
      customSurfaceName.value = '';
      hideMaterialLibraryForms();
      renderMaterialLibrary();
      updateMaterialTargetSummary();
    }}

    function registerBsdfAsset() {{
      ensureMaterialLibraryState();
      const file = bsdfFileInput && bsdfFileInput.files && bsdfFileInput.files.length ? bsdfFileInput.files[0] : null;
      if (!file) return;
      const asset = {{
        bsdf_asset_id: 'bsdf_' + Date.now(),
        name: file.name.replace(/\\.[^.]+$/, ''),
        file_name: file.name,
        status: 'registered'
      }};
      state.bsdfAssets.unshift(asset);
      state.materialDraft.bsdf_asset_id = asset.bsdf_asset_id;
      if (bsdfFileName) {{
        bsdfFileName.value = file.name;
      }}
      hideMaterialLibraryForms();
      renderMaterialLibrary();
      updateMaterialTargetSummary();
    }}

    function renderTransformRules() {{
      transformRuleList.innerHTML = '';
      if (!state.transformRules.length) {{
        transformManagerEmpty.classList.remove('hidden-block');
        transformSelectionSummary.textContent = 'Transform rule 없음. Components 탭에서 `Transform`을 눌러 시작하세요.';
        return;
      }}
      transformManagerEmpty.classList.add('hidden-block');
      ensureActiveTransformRule();
      for (const rule of state.transformRules) {{
        const row = document.createElement('div');
        row.className = 'manager-row' + (rule.rule_id === state.activeTransformRuleId ? ' active' : '');
        row.innerHTML =
          '<input type=\"checkbox\" data-rule-select=\"' + rule.rule_id + '\" ' + (state.selectedTransformRuleIds.has(rule.rule_id) ? 'checked' : '') + ' />'
          + '<div><div class=\"title\">' + rule.label + '</div><div class=\"meta\">'
          + 'Move X ' + rule.move.x.toFixed(3) + ' / Y ' + rule.move.y.toFixed(3) + ' / Z ' + rule.move.z.toFixed(3) + ' mm\\n'
          + 'Tilt Rx ' + rule.tilt.x.toFixed(3) + ' / Ry ' + rule.tilt.y.toFixed(3) + ' / Rz ' + rule.tilt.z.toFixed(3) + ' deg'
          + '</div></div>'
          + '<label class=\"toggle\"><input type=\"checkbox\" data-rule-enabled=\"' + rule.rule_id + '\" ' + (rule.enabled ? 'checked' : '') + ' /> on</label>';
        row.addEventListener('click', function (ev) {{
          const target = ev.target;
          if (target && (target.hasAttribute('data-rule-select') || target.hasAttribute('data-rule-enabled'))) {{
            return;
          }}
          state.activeTransformRuleId = rule.rule_id;
          syncEditorFromActiveRule();
          renderTransformRules();
          updateGapSelectionStats();
          showMovePopupAt(0, 0);
          drawViewer();
        }});
        transformRuleList.appendChild(row);
      }}
      const selectBoxes = transformRuleList.querySelectorAll('[data-rule-select]');
      for (const box of selectBoxes) {{
        box.addEventListener('change', function (ev) {{
          const ruleId = ev.target.getAttribute('data-rule-select');
          if (ev.target.checked) {{
            state.selectedTransformRuleIds.add(ruleId);
          }} else {{
            state.selectedTransformRuleIds.delete(ruleId);
          }}
          renderTransformRules();
          updateTransformSelectionSummary();
        }});
      }}
      const enableBoxes = transformRuleList.querySelectorAll('[data-rule-enabled]');
      for (const box of enableBoxes) {{
        box.addEventListener('change', function (ev) {{
          const rule = getTransformRuleById(ev.target.getAttribute('data-rule-enabled'));
          if (!rule) return;
          rule.enabled = !!ev.target.checked;
          updateGapSelectionStats();
          drawViewer();
        }});
      }}
      updateTransformSelectionSummary();
    }}

    function updateTransformSelectionSummary() {{
      const activeRule = activeTransformRule();
      const checkedCount = state.selectedTransformRuleIds.size;
      if (!activeRule) {{
        transformSelectionSummary.textContent = 'Transform rule 없음. Components 탭에서 `Transform`을 눌러 시작하세요.';
        return;
      }}
      transformSelectionSummary.textContent =
        'Active: ' + activeRule.label + '\\n'
        + 'Checked rules: ' + checkedCount + '\\n'
        + '입력은 오른쪽 3D viewer transform 창에서 수정 후 Apply로 확정합니다.';
    }}

    function syncEditorFromActiveRule() {{
      const rule = activeTransformRule();
      if (!rule || state.gapTargetMode !== 'component_move_gap') {{
        return;
      }}
      state.gapMove = cloneVector(rule.move);
      state.gapMoveText = {{
        x: String(rule.move.x),
        y: String(rule.move.y),
        z: String(rule.move.z)
      }};
      state.gapTilt = cloneVector(rule.tilt);
      state.gapTiltText = {{
        x: String(rule.tilt.x),
        y: String(rule.tilt.y),
        z: String(rule.tilt.z)
      }};
      syncTransformInputs();
    }}

    function updateRoiStats() {{
      roiStat.textContent = 'Selected Face Count: ' + state.selectedFaces.size;
    }}

    function updateSelectionModeUI() {{
      const mode = state.roiSelectionMode;
      const dragSelect = state.gapSelectionMethod === 'drag_box';
      if (state.gapTargetMode === 'face_gap') {{
        roiModeHint.textContent = '현재는 Local face move 선택 모드입니다. ROI는 선택사항이며, 3D viewer 클릭은 local face target 선택으로 동작합니다.';
        viewerTip.textContent = dragSelect
          ? 'Drag = local face 박스 선택, Ctrl+Drag = add/remove, Middle drag = rotate, Wheel = zoom, Right click = component menu, Right drag = pan.'
          : 'Drag/Middle drag = rotate, Wheel = zoom, Right click = component menu, Right drag = pan, Shift/Alt+drag = roll. 선택 모드에서만 Click 선택.';
        componentSelectBlock.classList.add('hidden-block');
        faceIndexBlock.classList.add('hidden-block');
        return;
      }}
      if (mode === 'click') {{
        roiModeHint.textContent = '3D view에서 선택: 지금부터 3D viewer 클릭이 ROI 선택으로 동작합니다.';
        viewerTip.textContent = dragSelect
          ? 'Drag = gap target 박스 선택, Ctrl+Drag = add/remove, Middle drag = rotate, Wheel = zoom, Right click = component menu, Right drag = pan.'
          : 'Drag/Middle drag = rotate, Wheel = zoom, Right click = component menu, Right drag = pan, Shift/Alt+drag = roll. ROI 모드에서만 Click 선택.';
        componentSelectBlock.classList.add('hidden-block');
        faceIndexBlock.classList.add('hidden-block');
      }} else if (mode === 'panel') {{
        roiModeHint.textContent = 'Component 선택: component 체크 또는 face index 입력으로 ROI를 선택합니다.';
        viewerTip.textContent = dragSelect
          ? 'Drag = gap target 박스 선택, Ctrl+Drag = add/remove, Middle drag = rotate, Wheel = zoom, Right click = component menu, Right drag = pan.'
          : 'Drag/Middle drag = rotate, Wheel = zoom, Right click = component menu, Right drag = pan, Shift/Alt+drag = roll, Camera preset = 정면/측면 보기 고정.';
        componentSelectBlock.classList.remove('hidden-block');
        faceIndexBlock.classList.remove('hidden-block');
      }} else {{
        roiModeHint.textContent = 'ROI 선택 방식이 아직 정해지지 않았습니다. 현재 3D viewer 클릭은 하이라이트만 동작합니다.';
        viewerTip.textContent = dragSelect
          ? 'Drag = gap target 박스 선택, Ctrl+Drag = add/remove, Middle drag = rotate, Wheel = zoom, Right click = component menu, Right drag = pan.'
          : 'Drag/Middle drag = rotate, Wheel = zoom, Right click = component menu, Right drag = pan, Shift/Alt+drag = roll, Camera preset = 정면/측면 보기 고정.';
        componentSelectBlock.classList.add('hidden-block');
        faceIndexBlock.classList.add('hidden-block');
      }}
    }}

    function recomputeSelectedFaces() {{
      let merged = [];
      if (state.roiSelectionMode === 'click') {{
        merged = Array.from(state.clickedFaces);
      }} else if (state.roiSelectionMode === 'panel') {{
        merged = Array.from(state.panelFaces);
      }} else {{
        merged = [];
      }}
      state.selectedFaces = new Set(uniqueSorted(merged.filter(faceIndex => !isFaceDeleted(faceIndex))));
      updateRoiStats();
      updateViewerMode();
      updateGapSelectionStats();
      drawViewer();
    }}

    function refreshSelectionFromObject() {{
      let faces = [];
      for (const id of state.selectedObjectIds) {{
        const item = state.objectsById.get(id);
        if (item && !isComponentDeleted(id) && !item.is_truncated) {{
          faces = faces.concat(item.face_indices);
        }}
      }}
      const manual = parseFaceList(roiInput.value);
      state.panelFaces = new Set(uniqueSorted(faces.concat(manual)));
      recomputeSelectedFaces();
    }}

    function resetRoiSelection() {{
      state.selectedFaces = new Set();
      state.clickedFaces = new Set();
      state.panelFaces = new Set();
      state.inspectedFaceIndex = null;
      state.selectedObjectIds.clear();
      roiInput.value = '';
      const checkboxes = objectList.querySelectorAll('input[type=\"checkbox\"]');
      for (const box of checkboxes) {{
        box.checked = false;
      }}
      updateRoiStats();
    }}

    function updateViewerMode() {{
      const roiActive = state.selectedFaces.size > 0;
      viewerStage.className = roiActive ? 'viewer-stage mode-roi' : 'viewer-stage mode-full';
      if (roiActive) {{
        fullViewHint.textContent = 'Mini map with ROI location';
        roiViewHint.textContent = 'Selected ROI promoted to main view';
      }} else {{
        fullViewHint.textContent = 'Imported model';
        roiViewHint.textContent = 'ROI preview';
      }}
    }}

    function renderModeLabel(mode) {{
      if (mode === 'surface') return 'Surface';
      if (mode === 'surface_edges') return 'Surface + Edge';
      return 'Wireframe';
    }}

    function updateRenderModeUI() {{
      const buttons = renderModeGroup.querySelectorAll('.mode-btn');
      for (const button of buttons) {{
        const active = button.getAttribute('data-render-mode') === state.renderMode;
        button.classList.toggle('active', active);
      }}
      renderModeBadge.textContent = renderModeLabel(state.renderMode);
      fullViewHint.textContent = 'Imported model / ' + renderModeLabel(state.renderMode);
    }}

    function updateViewerEngineUI() {{
      const buttons = viewerEngineGroup.querySelectorAll('.mode-btn');
      for (const button of buttons) {{
        const active = button.getAttribute('data-viewer-engine') === state.viewerEngine;
        button.classList.toggle('active', active);
      }}
      const useThree = state.viewerEngine === 'three' && !!window.LeakageThreeViewer;
      fullViewerCard.classList.toggle('three-active', useThree);
      roiViewerCard.classList.toggle('three-active', useThree);
      if (state.viewerEngine === 'three' && !window.LeakageThreeViewer) {{
        fullViewHint.textContent = 'Three.js viewer loading...';
      }} else if (useThree) {{
        fullViewHint.textContent = 'Imported model / Three.js / ' + renderModeLabel(state.renderMode);
      }}
    }}

    function ensureThreeRenderers() {{
      if (!window.LeakageThreeViewer) return false;
      if (!threeFullRenderer) {{
        threeFullRenderer = window.LeakageThreeViewer.create(threeFullViewer, 'full');
      }}
      if (!threeRoiRenderer) {{
        threeRoiRenderer = window.LeakageThreeViewer.create(threeRoiViewer, 'roi');
      }}
      return true;
    }}

    function syncThreeViewer() {{
      if (state.viewerEngine !== 'three') return;
      if (!state.mesh) {{
        updateViewerEngineUI();
        return;
      }}
      if (!ensureThreeRenderers()) {{
        updateViewerEngineUI();
        return;
      }}
      const payload = {{ mesh: state.mesh }};
      const options = {{
        renderMode: state.renderMode,
        selectedFaces: uniqueSorted(Array.from(state.selectedFaces)),
        hiddenFaces: getThreeHiddenFaces(),
        overlays: buildThreeTransformOverlays(),
        axisScalePercent: state.axisScalePercent,
        pickBaseOnly: state.emitterSelectionActive || state.receiverSelectionActive,
      }};
      threeFullRenderer.setScene(payload, options);
      threeRoiRenderer.setScene(payload, options);
      if (pendingThreeCameraPreset) {{
        threeFullRenderer.applyCameraPreset(pendingThreeCameraPreset);
        threeRoiRenderer.applyCameraPreset(pendingThreeCameraPreset);
        pendingThreeCameraPreset = null;
      }}
      updateViewerEngineUI();
    }}

    function getThreeHiddenFaces() {{
      const hiddenFaces = getCommittedTransformFaceSet();
      for (const faceIndex of getViewerExcludedFaceSet()) {{
        hiddenFaces.add(faceIndex);
      }}
      return uniqueSorted(Array.from(hiddenFaces));
    }}

    function modelSpanMm() {{
      if (!state.mesh || !state.mesh.vertices || !state.mesh.vertices.length) return 10;
      let minX = Infinity, minY = Infinity, minZ = Infinity;
      let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
      for (const vertex of state.mesh.vertices) {{
        minX = Math.min(minX, vertex[0]); maxX = Math.max(maxX, vertex[0]);
        minY = Math.min(minY, vertex[1]); maxY = Math.max(maxY, vertex[1]);
        minZ = Math.min(minZ, vertex[2]); maxZ = Math.max(maxZ, vertex[2]);
      }}
      return Math.max(maxX - minX, maxY - minY, maxZ - minZ, 1);
    }}

    function modelCenterMm() {{
      if (!state.mesh || !state.mesh.vertices || !state.mesh.vertices.length) return [0, 0, 0];
      let minX = Infinity, minY = Infinity, minZ = Infinity;
      let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
      for (const vertex of state.mesh.vertices) {{
        minX = Math.min(minX, vertex[0]); maxX = Math.max(maxX, vertex[0]);
        minY = Math.min(minY, vertex[1]); maxY = Math.max(maxY, vertex[1]);
        minZ = Math.min(minZ, vertex[2]); maxZ = Math.max(maxZ, vertex[2]);
      }}
      return [(minX + maxX) * 0.5, (minY + maxY) * 0.5, (minZ + maxZ) * 0.5];
    }}

    function emitterOverlayTransform(faceIndices, normalFlip) {{
      const firstFace = faceIndices.length ? faceIndices[0] : null;
      const objectId = firstFace === null ? null : state.faceToObjectId.get(firstFace);
      const object = objectId === null || objectId === undefined ? null : state.objectsById.get(objectId);
      const rule = object ? getTransformRuleByObjectId(objectId) : null;
      const hasApplied = rule && rule.enabled && transformRuleHasAppliedTransform(rule);
      const move = hasApplied ? cloneVector(rule.move) : {{ x: 0, y: 0, z: 0 }};
      const tilt = hasApplied ? cloneVector(rule.tilt) : {{ x: 0, y: 0, z: 0 }};
      const pivot = object && object.face_indices ? computePivotForFaceIndices(object.face_indices) : computePivotForFaceIndices(faceIndices);
      const sourceOrigin = computePivotForFaceIndices(faceIndices);
      const arrowOrigin = transformPoint(sourceOrigin, pivot, move, tilt);
      let arrowDirection = rotateDirection(averageFaceNormal(faceIndices), tilt);
      if (normalFlip) arrowDirection = arrowDirection.map((value) => -value);
      return {{ pivot, move, tilt, arrowOrigin, arrowDirection }};
    }}

    function normalizeArray3(vector) {{
      const length = Math.sqrt(vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2]);
      if (length <= 1e-9) return null;
      return [vector[0] / length, vector[1] / length, vector[2] / length];
    }}

    function subtractArray3(a, b) {{ return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }}
    function addArray3(a, b) {{ return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]; }}
    function scaleArray3(vector, scale) {{ return [vector[0] * scale, vector[1] * scale, vector[2] * scale]; }}
    function dotArray3(a, b) {{ return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }}
    function crossArray3(a, b) {{
      return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    }}

    function polygonAreaMm2FromPoints(points) {{
      if (!Array.isArray(points) || points.length < 3) return 0;
      const origin = points[0];
      let area = 0;
      for (let index = 1; index < points.length - 1; index += 1) {{
        const first = subtractArray3(points[index], origin);
        const second = subtractArray3(points[index + 1], origin);
        const cross = crossArray3(first, second);
        area += 0.5 * Math.sqrt(dotArray3(cross, cross));
      }}
      return area;
    }}

    const REFERENCE_PLANARITY_TOLERANCE_MM = 0.05;

    function referencePlaneFromVertexPoints(rawPoints) {{
      const points = (rawPoints || []).filter(Boolean).slice(0, 6);
      if (points.length < 3) return {{ referencePoints: points, referenceSegments: [] }};
      const centroid = points.reduce((sum, point) => addArray3(sum, point), [0, 0, 0]).map((value) => value / points.length);
      let farthestPair = null;
      let farthestDistanceSquared = -1;
      for (let firstIndex = 0; firstIndex < points.length; firstIndex += 1) {{
        for (let secondIndex = firstIndex + 1; secondIndex < points.length; secondIndex += 1) {{
          const delta = subtractArray3(points[secondIndex], points[firstIndex]);
          const distanceSquared = dotArray3(delta, delta);
          if (distanceSquared > farthestDistanceSquared) {{
            farthestDistanceSquared = distanceSquared;
            farthestPair = [points[firstIndex], points[secondIndex]];
          }}
        }}
      }}
      if (!farthestPair || farthestDistanceSquared <= 1e-12) return {{ referencePoints: points, referenceSegments: [] }};
      const uAxis = normalizeArray3(subtractArray3(farthestPair[1], farthestPair[0]));
      if (!uAxis) return {{ referencePoints: points, referenceSegments: [] }};
      let bestVRaw = null;
      let bestPerpendicularSquared = -1;
      for (const point of points) {{
        const relative = subtractArray3(point, centroid);
        const perpendicular = subtractArray3(relative, scaleArray3(uAxis, dotArray3(relative, uAxis)));
        const perpendicularSquared = dotArray3(perpendicular, perpendicular);
        if (perpendicularSquared > bestPerpendicularSquared) {{
          bestPerpendicularSquared = perpendicularSquared;
          bestVRaw = perpendicular;
        }}
      }}
      if (!bestVRaw || bestPerpendicularSquared <= 1e-12) return {{ referencePoints: points, referenceSegments: [] }};
      const vAxis = normalizeArray3(bestVRaw);
      if (!vAxis) return {{ referencePoints: points, referenceSegments: [] }};
      const normal = normalizeArray3(crossArray3(uAxis, vAxis));
      if (!normal) return {{ referencePoints: points, referenceSegments: [] }};
      const uCoordinates = points.map((point) => dotArray3(subtractArray3(point, centroid), uAxis));
      const vCoordinates = points.map((point) => dotArray3(subtractArray3(point, centroid), vAxis));
      const minU = Math.min(...uCoordinates);
      const maxU = Math.max(...uCoordinates);
      const minV = Math.min(...vCoordinates);
      const maxV = Math.max(...vCoordinates);
      const center = addArray3(centroid, addArray3(scaleArray3(uAxis, (minU + maxU) * 0.5), scaleArray3(vAxis, (minV + maxV) * 0.5)));
      const planarityErrorMm = Math.max(...points.map((point) => Math.abs(dotArray3(subtractArray3(point, center), normal))));
      return {{
        center,
        uAxis,
        vAxis,
        normal,
        widthMm: Math.max(maxU - minU, 0.001),
        heightMm: Math.max(maxV - minV, 0.001),
        surfaceConstruction: 'rectangular_fit',
        polygonPoints: [],
        polygonAreaMm2: null,
        referencePoints: points,
        referenceSegments: [],
        planarityErrorMm
      }};
    }}

    function convexHull2d(points) {{
      const unique = [];
      const signatures = new Set();
      for (const point of points || []) {{
        const signature = point.x.toFixed(9) + ':' + point.y.toFixed(9);
        if (signatures.has(signature)) continue;
        signatures.add(signature);
        unique.push(point);
      }}
      unique.sort((first, second) => first.x - second.x || first.y - second.y);
      if (unique.length <= 2) return unique;
      const cross2d = (origin, first, second) =>
        (first.x - origin.x) * (second.y - origin.y) - (first.y - origin.y) * (second.x - origin.x);
      const lower = [];
      for (const point of unique) {{
        while (lower.length >= 2 && cross2d(lower[lower.length - 2], lower[lower.length - 1], point) <= 1e-12) lower.pop();
        lower.push(point);
      }}
      const upper = [];
      for (let index = unique.length - 1; index >= 0; index -= 1) {{
        const point = unique[index];
        while (upper.length >= 2 && cross2d(upper[upper.length - 2], upper[upper.length - 1], point) <= 1e-12) upper.pop();
        upper.push(point);
      }}
      lower.pop();
      upper.pop();
      return lower.concat(upper);
    }}

    function polygonPlaneFromVertexPoints(rawPoints) {{
      const base = referencePlaneFromVertexPoints(rawPoints);
      if (!base || !base.center || !base.uAxis || !base.vAxis) return base;
      const projected = (base.referencePoints || []).map((point, index) => {{
        const relative = subtractArray3(point, base.center);
        return {{
          x: dotArray3(relative, base.uAxis),
          y: dotArray3(relative, base.vAxis),
          sourceIndex: index
        }};
      }});
      const hull = convexHull2d(projected);
      if (hull.length < 3) return {{ ...base, surfaceConstruction: 'polygon_auto', polygonPoints: [], polygonAreaMm2: 0 }};
      const polygonPoints = hull.map((point) => addArray3(
        base.center,
        addArray3(scaleArray3(base.uAxis, point.x), scaleArray3(base.vAxis, point.y))
      ));
      let twiceArea = 0;
      for (let index = 0; index < hull.length; index += 1) {{
        const next = hull[(index + 1) % hull.length];
        twiceArea += hull[index].x * next.y - next.x * hull[index].y;
      }}
      return {{
        ...base,
        surfaceConstruction: 'polygon_auto',
        polygonPoints,
        polygonAreaMm2: Math.abs(twiceArea) * 0.5,
        boundaryVertexCount: hull.length,
        interiorPointCount: Math.max(0, projected.length - hull.length)
      }};
    }}

    function datumPlaneFromInputs() {{
      const center = [parseMoveFieldValue(emitterCenterX.value), parseMoveFieldValue(emitterCenterY.value), parseMoveFieldValue(emitterCenterZ.value)];
      const rotation = {{
        x: parseMoveFieldValue(emitterRotationX.value),
        y: parseMoveFieldValue(emitterRotationY.value),
        z: parseMoveFieldValue(emitterRotationZ.value)
      }};
      const uAxis = rotateDirection([1, 0, 0], rotation);
      const vAxis = rotateDirection([0, 1, 0], rotation);
      return {{
        center,
        uAxis,
        vAxis,
        normal: normalizeArray3(crossArray3(uAxis, vAxis)) || [0, 0, 1],
        widthMm: Math.max(0.001, Math.abs(parseMoveFieldValue(emitterWidthInput.value))),
        heightMm: Math.max(0.001, Math.abs(parseMoveFieldValue(emitterHeightInput.value))),
        referencePoints: [],
        referenceSegments: []
      }};
    }}

    function referencePlaneFromState() {{
      if (!state.mesh) return null;
      const mode = emitterReferenceModeSelect.value || 'three_vertices';
      let points = [];
      let referenceSegments = [];
      let origin = null;
      let uTarget = null;
      let vTarget = null;
      if (mode === 'two_edges') {{
        if (!state.emitterReferenceEdges.length) return {{ referencePoints: [], referenceSegments: [] }};
        referenceSegments = state.emitterReferenceEdges.map((edge) => [state.mesh.vertices[edge[0]], state.mesh.vertices[edge[1]]]);
        points = referenceSegments.flat();
        if (state.emitterReferenceEdges.length < 2) return {{ referencePoints: points, referenceSegments }};
        const first = referenceSegments[0];
        const second = referenceSegments[1];
        origin = first[0];
        uTarget = first[1];
        const firstMid = scaleArray3(addArray3(first[0], first[1]), 0.5);
        const secondMid = scaleArray3(addArray3(second[0], second[1]), 0.5);
        vTarget = addArray3(origin, subtractArray3(secondMid, firstMid));
      }} else {{
        points = state.emitterReferenceVertices.map((index) => state.mesh.vertices[index]).filter(Boolean);
        return emitterReferenceSurfaceSelect.value === 'polygon_auto'
          ? polygonPlaneFromVertexPoints(points)
          : referencePlaneFromVertexPoints(points);
      }}
      const uAxis = normalizeArray3(subtractArray3(uTarget, origin));
      if (!uAxis) return {{ referencePoints: points, referenceSegments }};
      const vRaw = subtractArray3(vTarget, origin);
      const vProjected = subtractArray3(vRaw, scaleArray3(uAxis, dotArray3(vRaw, uAxis)));
      const vAxis = normalizeArray3(vProjected);
      if (!vAxis) return {{ referencePoints: points, referenceSegments }};
      const uCoordinates = points.map((point) => dotArray3(subtractArray3(point, origin), uAxis));
      const vCoordinates = points.map((point) => dotArray3(subtractArray3(point, origin), vAxis));
      const minU = Math.min(...uCoordinates);
      const maxU = Math.max(...uCoordinates);
      const minV = Math.min(...vCoordinates);
      const maxV = Math.max(...vCoordinates);
      const widthMm = Math.max(maxU - minU, 0.001);
      const heightMm = Math.max(maxV - minV, 0.001);
      const center = addArray3(origin, addArray3(scaleArray3(uAxis, (minU + maxU) * 0.5), scaleArray3(vAxis, (minV + maxV) * 0.5)));
      return {{
        center,
        uAxis,
        vAxis,
        normal: normalizeArray3(crossArray3(uAxis, vAxis)) || [0, 0, 1],
        widthMm,
        heightMm,
        referencePoints: points,
        referenceSegments
      }};
    }}

    function emitterPlaneFromSpec(emitter) {{
      if (!emitter || emitter.emitter_type === 'face') return null;
      const polygonPoints = Array.isArray(emitter.polygon_vertices) ? emitter.polygon_vertices : [];
      return {{
        center: emitter.center || [0, 0, 0],
        uAxis: emitter.u_axis || [1, 0, 0],
        vAxis: emitter.v_axis || [0, 1, 0],
        normal: normalizeArray3(crossArray3(emitter.u_axis || [1, 0, 0], emitter.v_axis || [0, 1, 0])) || [0, 0, 1],
        widthMm: Number(emitter.width_mm) || 1,
        heightMm: Number(emitter.height_mm) || 1,
        surfaceConstruction: emitter.surface_construction || 'rectangular_fit',
        polygonPoints,
        polygonAreaMm2: Number(emitter.polygon_area_mm2) || polygonAreaMm2FromPoints(polygonPoints) || null,
        referencePoints: (emitter.reference_vertex_indices || []).map((index) => state.mesh.vertices[index]).filter(Boolean),
        referenceSegments: (emitter.reference_edge_vertex_indices || []).map((edge) => [state.mesh.vertices[edge[0]], state.mesh.vertices[edge[1]]]).filter((segment) => segment[0] && segment[1])
      }};
    }}

    function currentDraftEmitterPlane() {{
      if (state.emitterDraftType === 'datum_plane') return datumPlaneFromInputs();
      if (state.emitterDraftType === 'reference_plane') return referencePlaneFromState();
      return null;
    }}

    function receiverById(receiverId) {{
      return state.receivers.find((item) => item.receiver_id === receiverId) || null;
    }}

    function currentReceiver() {{
      return state.activeReceiverId ? receiverById(state.activeReceiverId) : null;
    }}

    function receiverDatumPlaneFromInputs() {{
      const center = [parseMoveFieldValue(receiverCenterX.value), parseMoveFieldValue(receiverCenterY.value), parseMoveFieldValue(receiverCenterZ.value)];
      const rotation = {{
        x: parseMoveFieldValue(receiverRotationX.value),
        y: parseMoveFieldValue(receiverRotationY.value),
        z: parseMoveFieldValue(receiverRotationZ.value)
      }};
      const uAxis = rotateDirection([1, 0, 0], rotation);
      const vAxis = rotateDirection([0, 1, 0], rotation);
      return {{
        center,
        uAxis,
        vAxis,
        normal: normalizeArray3(crossArray3(uAxis, vAxis)) || [0, 0, 1],
        widthMm: Math.max(0.001, Math.abs(parseMoveFieldValue(receiverWidthInput.value))),
        heightMm: Math.max(0.001, Math.abs(parseMoveFieldValue(receiverHeightInput.value))),
        referencePoints: [],
        referenceSegments: []
      }};
    }}

    function receiverReferencePlaneFromState() {{
      if (!state.mesh) return null;
      const mode = receiverReferenceModeSelect.value || 'three_vertices';
      let points = [];
      let referenceSegments = [];
      let origin = null;
      let uTarget = null;
      let vTarget = null;
      if (mode === 'two_edges') {{
        if (!state.receiverReferenceEdges.length) return {{ referencePoints: [], referenceSegments: [] }};
        referenceSegments = state.receiverReferenceEdges.map((edge) => [state.mesh.vertices[edge[0]], state.mesh.vertices[edge[1]]]);
        points = referenceSegments.flat();
        if (state.receiverReferenceEdges.length < 2) return {{ referencePoints: points, referenceSegments }};
        const first = referenceSegments[0];
        const second = referenceSegments[1];
        origin = first[0];
        uTarget = first[1];
        const firstMid = scaleArray3(addArray3(first[0], first[1]), 0.5);
        const secondMid = scaleArray3(addArray3(second[0], second[1]), 0.5);
        vTarget = addArray3(origin, subtractArray3(secondMid, firstMid));
      }} else {{
        points = state.receiverReferenceVertices.map((index) => state.mesh.vertices[index]).filter(Boolean);
        return referencePlaneFromVertexPoints(points);
      }}
      const uAxis = normalizeArray3(subtractArray3(uTarget, origin));
      if (!uAxis) return {{ referencePoints: points, referenceSegments }};
      const vRaw = subtractArray3(vTarget, origin);
      const vProjected = subtractArray3(vRaw, scaleArray3(uAxis, dotArray3(vRaw, uAxis)));
      const vAxis = normalizeArray3(vProjected);
      if (!vAxis) return {{ referencePoints: points, referenceSegments }};
      const uCoordinates = points.map((point) => dotArray3(subtractArray3(point, origin), uAxis));
      const vCoordinates = points.map((point) => dotArray3(subtractArray3(point, origin), vAxis));
      const minU = Math.min(...uCoordinates);
      const maxU = Math.max(...uCoordinates);
      const minV = Math.min(...vCoordinates);
      const maxV = Math.max(...vCoordinates);
      const widthMm = Math.max(maxU - minU, 0.001);
      const heightMm = Math.max(maxV - minV, 0.001);
      const center = addArray3(origin, addArray3(scaleArray3(uAxis, (minU + maxU) * 0.5), scaleArray3(vAxis, (minV + maxV) * 0.5)));
      return {{
        center,
        uAxis,
        vAxis,
        normal: normalizeArray3(crossArray3(uAxis, vAxis)) || [0, 0, 1],
        widthMm,
        heightMm,
        referencePoints: points,
        referenceSegments
      }};
    }}

    function captureCurrentViewReceiverPlane() {{
      if (!ensureThreeRenderers() || !threeFullRenderer || typeof threeFullRenderer.getCameraFrame !== 'function') return null;
      const distanceMm = Math.max(0.001, Math.abs(parseMoveFieldValue(receiverViewDistanceInput.value)) || Math.max(50, modelSpanMm() * 0.25));
      const frame = threeFullRenderer.getCameraFrame(distanceMm);
      if (!frame || !frame.center) return null;
      state.receiverCurrentViewPlane = {{
        center: frame.center,
        uAxis: frame.uAxis,
        vAxis: frame.vAxis,
        normal: frame.normal,
        widthMm: Math.max(0.001, Math.abs(parseMoveFieldValue(receiverWidthInput.value))),
        heightMm: Math.max(0.001, Math.abs(parseMoveFieldValue(receiverHeightInput.value))),
        referencePoints: [],
        referenceSegments: [],
        distanceMm: frame.distanceMm
      }};
      return state.receiverCurrentViewPlane;
    }}

    function receiverPlaneFromSpec(receiver) {{
      if (!receiver) return null;
      return {{
        center: receiver.center || [0, 0, 0],
        uAxis: receiver.u_axis || [1, 0, 0],
        vAxis: receiver.v_axis || [0, 1, 0],
        normal: receiver.normal || normalizeArray3(crossArray3(receiver.u_axis || [1, 0, 0], receiver.v_axis || [0, 1, 0])) || [0, 0, 1],
        widthMm: Number(receiver.width_mm) || 1,
        heightMm: Number(receiver.height_mm) || 1,
        referencePoints: (receiver.reference_vertex_indices || []).map((index) => state.mesh.vertices[index]).filter(Boolean),
        referenceSegments: (receiver.reference_edge_vertex_indices || []).map((edge) => [state.mesh.vertices[edge[0]], state.mesh.vertices[edge[1]]]).filter((segment) => segment[0] && segment[1]),
        distanceMm: receiver.view_distance_mm || null
      }};
    }}

    function receiverBasePlaneFromSpec(receiver) {{
      if (!receiver) return null;
      const hasStoredBase = Array.isArray(receiver.base_center)
        && Array.isArray(receiver.base_u_axis)
        && Array.isArray(receiver.base_v_axis);
      if (!hasStoredBase) return receiverPlaneFromSpec(receiver);
      const baseU = receiver.base_u_axis || [1, 0, 0];
      const baseV = receiver.base_v_axis || [0, 1, 0];
      return {{
        center: receiver.base_center,
        uAxis: baseU,
        vAxis: baseV,
        normal: receiver.base_normal || normalizeArray3(crossArray3(baseU, baseV)) || [0, 0, 1],
        widthMm: Number(receiver.width_mm) || 1,
        heightMm: Number(receiver.height_mm) || 1,
        referencePoints: (receiver.reference_vertex_indices || []).map((index) => state.mesh.vertices[index]).filter(Boolean),
        referenceSegments: (receiver.reference_edge_vertex_indices || []).map((edge) => [state.mesh.vertices[edge[0]], state.mesh.vertices[edge[1]]]).filter((segment) => segment[0] && segment[1]),
        distanceMm: receiver.view_distance_mm || null
      }};
    }}

    function receiverAdjustmentFromInputs() {{
      return {{
        offset: [
          parseMoveFieldValue(receiverOffsetX.value),
          parseMoveFieldValue(receiverOffsetY.value),
          parseMoveFieldValue(receiverOffsetZ.value)
        ],
        tilt: {{
          x: parseMoveFieldValue(receiverTiltX.value),
          y: parseMoveFieldValue(receiverTiltY.value),
          z: parseMoveFieldValue(receiverTiltZ.value)
        }}
      }};
    }}

    function applyReceiverAdjustment(basePlane) {{
      if (!basePlane || !basePlane.center) return basePlane;
      const adjustment = receiverAdjustmentFromInputs();
      const uAxis = rotateDirection(basePlane.uAxis || [1, 0, 0], adjustment.tilt);
      const vAxis = rotateDirection(basePlane.vAxis || [0, 1, 0], adjustment.tilt);
      return {{
        ...basePlane,
        center: addArray3(basePlane.center, adjustment.offset),
        uAxis,
        vAxis,
        normal: normalizeArray3(crossArray3(uAxis, vAxis)) || [0, 0, 1]
      }};
    }}

    function currentBaseDraftReceiverPlane() {{
      if (state.receiverDraftType === 'reference_plane') return receiverReferencePlaneFromState();
      if (state.receiverDraftType === 'current_view') {{
        if (state.receiverCurrentViewPlane) {{
          state.receiverCurrentViewPlane.widthMm = Math.max(0.001, Math.abs(parseMoveFieldValue(receiverWidthInput.value)));
          state.receiverCurrentViewPlane.heightMm = Math.max(0.001, Math.abs(parseMoveFieldValue(receiverHeightInput.value)));
        }}
        return state.receiverCurrentViewPlane;
      }}
      return receiverDatumPlaneFromInputs();
    }}

    function currentDraftReceiverPlane() {{
      const basePlane = currentBaseDraftReceiverPlane();
      if (state.receiverDraftType === 'datum_plane') return basePlane;
      return applyReceiverAdjustment(basePlane);
    }}

    function buildThreeTransformOverlays() {{
      const overlays = [];
      if (!state.mesh) return overlays;
      const arrowLength = Math.max(2, modelSpanMm() * 0.075);
      const emitterEditorVisible = !cursorEmitterPopup.classList.contains('hidden-block');
      for (const emitter of state.emitters) {{
        if (emitter.enabled === false) continue;
        if (emitterEditorVisible && emitter.emitter_id === state.activeEmitterId) continue;
        const isFaceEmitter = emitter.emitter_type === 'face';
        const emitterFaces = isFaceEmitter ? visibleFaceIndices(emitter.face_indices) : [];
        if (isFaceEmitter && !emitterFaces.length) continue;
        const faceGeometry = isFaceEmitter ? emitterOverlayTransform(emitterFaces, emitter.normal_flip) : null;
        const virtualPlane = isFaceEmitter ? null : emitterPlaneFromSpec(emitter);
        if (!isFaceEmitter && (!virtualPlane || !virtualPlane.center)) continue;
        let arrowDirection = isFaceEmitter ? faceGeometry.arrowDirection : virtualPlane.normal;
        if (!isFaceEmitter && emitter.normal_flip) arrowDirection = arrowDirection.map((value) => -value);
        overlays.push({{
          kind: 'emitter_' + emitter.emitter_id,
          faceIndices: emitterFaces,
          pivot: faceGeometry ? faceGeometry.pivot : [0, 0, 0],
          move: faceGeometry ? faceGeometry.move : {{ x: 0, y: 0, z: 0 }},
          tilt: faceGeometry ? faceGeometry.tilt : {{ x: 0, y: 0, z: 0 }},
          virtualPlane,
          referencePoints: virtualPlane ? virtualPlane.referencePoints : [],
          referenceSegments: virtualPlane ? virtualPlane.referenceSegments : [],
          color: emitter.emitter_id === state.activeEmitterId ? 0xfb923c : 0xf97316,
          edgeColor: 0xffedd5,
          opacity: emitter.emitter_id === state.activeEmitterId ? 0.56 : 0.42,
          edgeOpacity: 1.0,
          arrowOrigin: faceGeometry ? faceGeometry.arrowOrigin : virtualPlane.center,
          arrowDirection,
          arrowLength,
          arrowColor: 0xfbbf24
        }});
      }}
      const draftEmitterFaces = visibleFaceIndices(uniqueSorted(Array.from(state.emitterDraftFaces)));
      const draftVirtualPlane = emitterEditorVisible ? currentDraftEmitterPlane() : null;
      if ((draftEmitterFaces.length || draftVirtualPlane) && emitterEditorVisible) {{
        const geometry = draftEmitterFaces.length ? emitterOverlayTransform(draftEmitterFaces, !!emitterNormalFlipInput.checked) : null;
        let draftDirection = geometry ? geometry.arrowDirection : (draftVirtualPlane && draftVirtualPlane.normal ? draftVirtualPlane.normal : null);
        if (!geometry && draftDirection && emitterNormalFlipInput.checked) draftDirection = draftDirection.map((value) => -value);
        overlays.push({{
          kind: 'emitter_draft',
          faceIndices: draftEmitterFaces,
          pivot: geometry ? geometry.pivot : [0, 0, 0],
          move: geometry ? geometry.move : {{ x: 0, y: 0, z: 0 }},
          tilt: geometry ? geometry.tilt : {{ x: 0, y: 0, z: 0 }},
          virtualPlane: draftVirtualPlane && draftVirtualPlane.center ? draftVirtualPlane : null,
          referencePoints: draftVirtualPlane ? draftVirtualPlane.referencePoints : [],
          referenceSegments: draftVirtualPlane ? draftVirtualPlane.referenceSegments : [],
          color: 0xfacc15,
          edgeColor: 0xfef3c7,
          opacity: 0.62,
          edgeOpacity: 1.0,
          arrowOrigin: geometry ? geometry.arrowOrigin : (draftVirtualPlane ? draftVirtualPlane.center : null),
          arrowDirection: draftDirection,
          arrowLength,
          arrowColor: 0xfde047
        }});
      }}
      const receiverEditorVisible = !cursorReceiverPopup.classList.contains('hidden-block');
      for (const receiver of state.receivers) {{
        if (receiver.enabled === false) continue;
        if (receiverEditorVisible && receiver.receiver_id === state.activeReceiverId) continue;
        const receiverPlane = receiverPlaneFromSpec(receiver);
        if (!receiverPlane || !receiverPlane.center) continue;
        let receiverDirection = receiverPlane.normal || [0, 0, 1];
        if (receiver.normal_flip) receiverDirection = receiverDirection.map((value) => -value);
        overlays.push({{
          kind: 'receiver_' + receiver.receiver_id,
          faceIndices: [],
          pivot: [0, 0, 0],
          move: {{ x: 0, y: 0, z: 0 }},
          tilt: {{ x: 0, y: 0, z: 0 }},
          virtualPlane: receiverPlane,
          referencePoints: receiverPlane.referencePoints || [],
          referenceSegments: receiverPlane.referenceSegments || [],
          color: receiver.receiver_id === state.activeReceiverId ? 0xc084fc : 0xa855f7,
          edgeColor: 0xf3e8ff,
          opacity: receiver.receiver_id === state.activeReceiverId ? 0.50 : 0.34,
          edgeOpacity: 1.0,
          arrowOrigin: receiverPlane.center,
          arrowDirection: receiverDirection,
          arrowLength,
          arrowColor: 0xd8b4fe
        }});
      }}
      const draftReceiverPlane = receiverEditorVisible ? currentDraftReceiverPlane() : null;
      if (draftReceiverPlane && receiverEditorVisible) {{
        let draftReceiverDirection = draftReceiverPlane.normal || null;
        if (draftReceiverDirection && receiverNormalFlipInput.checked) draftReceiverDirection = draftReceiverDirection.map((value) => -value);
        overlays.push({{
          kind: 'receiver_draft',
          faceIndices: [],
          pivot: [0, 0, 0],
          move: {{ x: 0, y: 0, z: 0 }},
          tilt: {{ x: 0, y: 0, z: 0 }},
          virtualPlane: draftReceiverPlane.center ? draftReceiverPlane : null,
          referencePoints: draftReceiverPlane.referencePoints || [],
          referenceSegments: draftReceiverPlane.referenceSegments || [],
          color: 0xc084fc,
          edgeColor: 0xf3e8ff,
          opacity: 0.56,
          edgeOpacity: 1.0,
          arrowOrigin: draftReceiverPlane.center || null,
          arrowDirection: draftReceiverDirection,
          arrowLength,
          arrowColor: 0xe9d5ff
        }});
      }}
      for (const objectId of uniqueSorted(Array.from(state.selectedGapObjectIds))) {{
        if (isComponentUnavailable(objectId)) continue;
        const object = state.objectsById.get(objectId);
        if (!object || !object.face_indices || !object.face_indices.length) continue;
        const rule = getTransformRuleByObjectId(objectId);
        const hasApplied = rule && rule.enabled && transformRuleHasAppliedTransform(rule);
        overlays.push({{
          kind: 'selected_component_' + objectId,
          faceIndices: object.face_indices,
          pivot: computePivotForFaceIndices(object.face_indices),
          move: hasApplied ? cloneVector(rule.move) : {{ x: 0, y: 0, z: 0 }},
          tilt: hasApplied ? cloneVector(rule.tilt) : {{ x: 0, y: 0, z: 0 }},
          color: objectId === state.selectedGapObjectId ? 0x38bdf8 : 0x60a5fa,
          edgeColor: 0xe0f2fe,
          opacity: objectId === state.selectedGapObjectId ? 0.30 : 0.22,
          edgeOpacity: 0.90
        }});
      }}
      if (state.selectedMaterialObjectId !== null && state.selectedMaterialObjectId !== undefined) {{
        const object = state.objectsById.get(state.selectedMaterialObjectId);
        if (!isComponentUnavailable(state.selectedMaterialObjectId) && object && object.face_indices && object.face_indices.length) {{
          const rule = getTransformRuleByObjectId(state.selectedMaterialObjectId);
          const hasApplied = rule && rule.enabled && transformRuleHasAppliedTransform(rule);
          overlays.push({{
            kind: 'selected_material_' + state.selectedMaterialObjectId,
            faceIndices: object.face_indices,
            pivot: computePivotForFaceIndices(object.face_indices),
            move: hasApplied ? cloneVector(rule.move) : {{ x: 0, y: 0, z: 0 }},
            tilt: hasApplied ? cloneVector(rule.tilt) : {{ x: 0, y: 0, z: 0 }},
            color: 0x2dd4bf,
            edgeColor: 0x99f6e4,
            opacity: 0.24,
            edgeOpacity: 0.88
          }});
        }}
      }}
      for (const rule of state.transformRules) {{
        if (!rule.enabled || rule.target_type !== 'component' || !transformRuleHasAppliedTransform(rule)) continue;
        if (isComponentUnavailable(rule.object_id)) continue;
        const object = state.objectsById.get(rule.object_id);
        if (!object || !object.face_indices || !object.face_indices.length) continue;
        overlays.push({{
          kind: rule.rule_id === state.activeTransformRuleId ? 'applied_active' : 'applied',
          faceIndices: object.face_indices,
          pivot: computePivotForFaceIndices(object.face_indices),
          move: cloneVector(rule.move),
          tilt: cloneVector(rule.tilt),
          color: rule.rule_id === state.activeTransformRuleId ? 0xef4444 : 0xdc2626,
          edgeColor: 0xfca5a5,
          opacity: rule.rule_id === state.activeTransformRuleId ? 0.52 : 0.42,
          edgeOpacity: 0.98
        }});
      }}
      if (state.gapTargetMode === 'component_move_gap') {{
        const rule = activeTransformRule();
        const object = rule ? state.objectsById.get(rule.object_id) : null;
        if (
          state.previewOverlayEnabled
          && rule
          && object
          && !isComponentUnavailable(rule.object_id)
          && object.face_indices
          && object.face_indices.length
          && activeEditorDiffersFromRule()
        ) {{
          overlays.push({{
            kind: 'draft',
            faceIndices: object.face_indices,
            pivot: computePivotForFaceIndices(object.face_indices),
            move: cloneVector(state.gapMove),
            tilt: cloneVector(state.gapTilt),
            color: 0xfacc15,
            edgeColor: 0xfef08a,
            opacity: 0.32,
            edgeOpacity: 0.95
          }});
        }}
      }} else if (state.previewOverlayEnabled) {{
        const faceIndices = visibleFaceIndices(getActivePreviewFaceIndices());
        if (faceIndices.length && (currentMoveMagnitude() > 1e-9 || currentTiltMagnitude() > 1e-9)) {{
          overlays.push({{
            kind: 'draft_face',
            faceIndices,
            pivot: computePivotForFaceIndices(faceIndices),
            move: cloneVector(state.gapMove),
            tilt: cloneVector(state.gapTilt),
            color: 0xfacc15,
            edgeColor: 0xfef08a,
            opacity: 0.32,
            edgeOpacity: 0.95
          }});
        }}
      }}
      const directPaths = state.rayTraceResult && Array.isArray(state.rayTraceResult.stored_paths)
        ? state.rayTraceResult.stored_paths
        : [];
      const visiblePaths = directPaths
        .filter((path) => Array.isArray(path) && path.length >= 2 && path[0].point && path[path.length - 1].point)
        .slice(0, 500);
      const directReceiverSegments = [];
      const reflectedReceiverSegments = [];
      const primarySurfaceSegments = [];
      const reflectedSegments = {{
        specular: [],
        lambertian: [],
        gaussian: []
      }};
      for (const path of visiblePaths) {{
        const reachesReceiver = rayPathReachesReceiver(path);
        const receiverFilter = reachesReceiver ? receiverPathFilterName(path) : null;
        if (receiverFilter && !state.rayDisplayFilters[receiverFilter]) continue;
        for (let index = 1; index < path.length; index++) {{
          const startEvent = path[index - 1];
          const endEvent = path[index];
          if (!startEvent?.point || !endEvent?.point) continue;
          const segment = [startEvent.point, endEvent.point];
          if (reachesReceiver) {{
            if (receiverFilter === 'receiver_reflected') reflectedReceiverSegments.push(segment);
            else directReceiverSegments.push(segment);
            continue;
          }}
          if (index === 1) {{
            if (state.rayDisplayFilters.direct) {{
              primarySurfaceSegments.push(segment);
            }}
            continue;
          }}
          const rayKind = String(endEvent.ray_kind || startEvent.ray_kind || 'gaussian');
          if (reflectedSegments[rayKind] && state.rayDisplayFilters[rayKind]) {{
            reflectedSegments[rayKind].push(segment);
          }}
        }}
      }}
      if (directReceiverSegments.length) {{
        overlays.push({{
          kind: 'direct_receiver_ray_paths',
          faceIndices: [],
          referencePoints: [],
          referenceSegments: directReceiverSegments,
          segmentColor: 0x4ade80,
          segmentOpacity: 1.0
        }});
      }}
      if (reflectedReceiverSegments.length) {{
        overlays.push({{
          kind: 'reflected_receiver_ray_paths',
          faceIndices: [],
          referencePoints: [],
          referenceSegments: reflectedReceiverSegments,
          segmentColor: 0xfacc15,
          segmentOpacity: 1.0
        }});
      }}
      if (primarySurfaceSegments.length) {{
        overlays.push({{
          kind: 'primary_surface_paths',
          faceIndices: [],
          referencePoints: [],
          referenceSegments: primarySurfaceSegments,
          segmentColor: 0x60a5fa,
          segmentOpacity: 0.62
        }});
      }}
      const reflectionColors = {{
        specular: 0xfb923c,
        lambertian: 0xc084fc,
        gaussian: 0x22d3ee
      }};
      for (const rayKind of Object.keys(reflectedSegments)) {{
        if (!reflectedSegments[rayKind].length) continue;
        overlays.push({{
          kind: 'reflected_' + rayKind + '_paths',
          faceIndices: [],
          referencePoints: [],
          referenceSegments: reflectedSegments[rayKind],
          segmentColor: reflectionColors[rayKind],
          segmentOpacity: 0.76
        }});
      }}
      return overlays;
    }}

    function buildFaceAdjacency() {{
      state.faceAdjacency = new Map();
      if (!state.mesh || !state.mesh.faces) return;
      const edgeMap = new Map();
      for (let faceIndex = 0; faceIndex < state.mesh.faces.length; faceIndex++) {{
        const tri = state.mesh.faces[faceIndex];
        const edges = [
          [tri[0], tri[1]],
          [tri[1], tri[2]],
          [tri[2], tri[0]]
        ];
        for (const edge of edges) {{
          const a = Math.min(edge[0], edge[1]);
          const b = Math.max(edge[0], edge[1]);
          const key = a + ':' + b;
          if (!edgeMap.has(key)) {{
            edgeMap.set(key, []);
          }}
          edgeMap.get(key).push(faceIndex);
        }}
      }}
      for (const faceIndices of edgeMap.values()) {{
        for (let i = 0; i < faceIndices.length; i++) {{
          const a = faceIndices[i];
          if (!state.faceAdjacency.has(a)) {{
            state.faceAdjacency.set(a, new Set());
          }}
          for (let j = i + 1; j < faceIndices.length; j++) {{
            const b = faceIndices[j];
            if (!state.faceAdjacency.has(b)) {{
              state.faceAdjacency.set(b, new Set());
            }}
            state.faceAdjacency.get(a).add(b);
            state.faceAdjacency.get(b).add(a);
          }}
        }}
      }}
    }}

    function buildProjectedScene(canvas) {{
      if (!state.mesh) return null;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      const verts = state.mesh.vertices;
      const faces = state.mesh.faces;
      if (!verts.length || !faces.length || !w || !h) return null;

      let minX = Infinity, minY = Infinity, minZ = Infinity;
      let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
      for (const v of verts) {{
        minX = Math.min(minX, v[0]); maxX = Math.max(maxX, v[0]);
        minY = Math.min(minY, v[1]); maxY = Math.max(maxY, v[1]);
        minZ = Math.min(minZ, v[2]); maxZ = Math.max(maxZ, v[2]);
      }}
      const cx = (minX + maxX) * 0.5;
      const cy = (minY + maxY) * 0.5;
      const cz = (minZ + maxZ) * 0.5;
      const span = Math.max(maxX - minX, maxY - minY, maxZ - minZ, 1.0);

      const sinY = Math.sin(state.transform.yaw);
      const cosY = Math.cos(state.transform.yaw);
      const sinP = Math.sin(state.transform.pitch);
      const cosP = Math.cos(state.transform.pitch);
      const scale = Math.min(w, h) / (span * 3.0);
      const distance = span * state.transform.distance;

      function projectWorldPoint(worldPoint) {{
        let x = worldPoint[0] - cx;
        let y = worldPoint[1] - cy;
        let z = worldPoint[2] - cz;
        const x1 = x * cosY + z * sinY;
        const z1 = -x * sinY + z * cosY;
        const y1 = y * cosP - z1 * sinP;
        const z2 = y * sinP + z1 * cosP;
        const depth = z2 + distance;
        const f = 420.0;
        return {{
          x: x1,
          y: y1,
          z: z2,
          depth: depth,
          screenX: w / 2 + (x1 * f * scale) / Math.max(0.2, depth),
          screenY: h / 2 + (y1 * f * scale) / Math.max(0.2, depth)
        }};
      }}

      const projected = [];
      for (const p of verts) {{
        projected.push(projectWorldPoint(p));
      }}

      const triList = [];
      const lightDir = {{ x: -0.35, y: -0.45, z: 0.82 }};
      for (let i = 0; i < faces.length; i++) {{
        const tri = faces[i];
        const pa = projected[tri[0]];
        const pb = projected[tri[1]];
        const pc = projected[tri[2]];
        const area = Math.abs((pb.screenX - pa.screenX) * (pc.screenY - pa.screenY) - (pb.screenY - pa.screenY) * (pc.screenX - pa.screenX)) / 2;
        if (area < 0.1) continue;
        const ux = pb.x - pa.x;
        const uy = pb.y - pa.y;
        const uz = pb.z - pa.z;
        const vx = pc.x - pa.x;
        const vy = pc.y - pa.y;
        const vz = pc.z - pa.z;
        const nx = uy * vz - uz * vy;
        const ny = uz * vx - ux * vz;
        const nz = ux * vy - uy * vx;
        const nLen = Math.max(1e-6, Math.sqrt(nx * nx + ny * ny + nz * nz));
        const ndotl = Math.max(0.18, Math.min(1.0, (nx * lightDir.x + ny * lightDir.y + nz * lightDir.z) / nLen));
        triList.push({{ idx: i, p0: pa, p1: pb, p2: pc, depth: (pa.z + pb.z + pc.z) / 3.0, shade: ndotl }});
      }}
      triList.sort((a, b) => b.depth - a.depth);
      const axisLen = Math.max(span * 0.18, 1.0);
      return {{
        w,
        h,
        triList,
        projectPoint: projectWorldPoint,
        span: span,
        bboxMin: [minX, minY, minZ],
        bboxMax: [maxX, maxY, maxZ],
        center: [cx, cy, cz],
        origin: projectWorldPoint([0.0, 0.0, 0.0]),
        axes: {{
          origin: projectWorldPoint([0.0, 0.0, 0.0]),
          x: projectWorldPoint([axisLen, 0.0, 0.0]),
          y: projectWorldPoint([0.0, axisLen, 0.0]),
          z: projectWorldPoint([0.0, 0.0, axisLen]),
        }},
        cornerAxes: {{
          x: projectWorldPoint([cx + axisLen, cy, cz]),
          y: projectWorldPoint([cx, cy + axisLen, cz]),
          z: projectWorldPoint([cx, cy, cz + axisLen]),
          c: projectWorldPoint([cx, cy, cz])
        }}
      }};
    }}

    function updateCoordReadout(scene) {{
      if (!scene) {{
        coordSummary.textContent = 'No model loaded';
        coordReadout.textContent = 'Origin: (0, 0, 0)';
        return;
      }}
      const bboxMin = scene.bboxMin.map(v => Number(v).toFixed(2)).join(', ');
      const bboxMax = scene.bboxMax.map(v => Number(v).toFixed(2)).join(', ');
      const center = scene.center.map(v => Number(v).toFixed(2)).join(', ');
      const size = [
        scene.bboxMax[0] - scene.bboxMin[0],
        scene.bboxMax[1] - scene.bboxMin[1],
        scene.bboxMax[2] - scene.bboxMin[2]
      ].map(v => Number(v).toFixed(2)).join(' × ');
      coordSummary.textContent = 'Center (' + center + ') · Size ' + size + ' mm';
      coordReadout.textContent = 'Origin: (0.00, 0.00, 0.00)\\nCenter: (' + center + ')\\nBBox min: (' + bboxMin + ')\\nBBox max: (' + bboxMax + ')';
    }}

    function drawAxisOverlay(ctx, scene, w, h) {{
      if (!scene || !scene.axes) return;
      const origin = scene.axes.origin;
      const zoomFactor = Math.max(0.45, Math.min(1.9, state.transform.distance));
      const manualScale = Math.max(0.5, Math.min(1.5, state.axisScalePercent / 100.0));
      const mainAxisPixels = Math.max(24, Math.min(82, 42 * zoomFactor * manualScale));
      if (origin.depth > 0.1 && origin.screenX >= -80 && origin.screenX <= w + 80 && origin.screenY >= -80 && origin.screenY <= h + 80) {{
        drawAxisLine(ctx, origin, scene.axes.x, '#ef4444', 'X', mainAxisPixels);
        drawAxisLine(ctx, origin, scene.axes.y, '#22c55e', 'Y', mainAxisPixels);
        drawAxisLine(ctx, origin, scene.axes.z, '#3b82f6', 'Z', mainAxisPixels);
        ctx.fillStyle = '#f8fafc';
        ctx.beginPath();
        ctx.arc(origin.screenX, origin.screenY, 3.5, 0, Math.PI * 2);
        ctx.fill();
      }}

      const baseX = 70;
      const baseY = h - 60;
      const c = scene.cornerAxes.c;
      const sx = scene.cornerAxes.x;
      const sy = scene.cornerAxes.y;
      const sz = scene.cornerAxes.z;
      drawMiniAxis(ctx, baseX, baseY, sx.x - c.x, sx.y - c.y, '#ef4444', 'X');
      drawMiniAxis(ctx, baseX, baseY, sy.x - c.x, sy.y - c.y, '#22c55e', 'Y');
      drawMiniAxis(ctx, baseX, baseY, sz.x - c.x, sz.y - c.y, '#3b82f6', 'Z');
      ctx.fillStyle = '#f8fafc';
      ctx.beginPath();
      ctx.arc(baseX, baseY, 3, 0, Math.PI * 2);
      ctx.fill();
    }}

    function drawAxisLine(ctx, from, to, color, label, pixelLength) {{
      const dx = to.screenX - from.screenX;
      const dy = to.screenY - from.screenY;
      const len = Math.max(1e-6, Math.sqrt(dx * dx + dy * dy));
      const ux = dx / len;
      const uy = dy / len;
      const ex = from.screenX + ux * pixelLength;
      const ey = from.screenY + uy * pixelLength;
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(from.screenX, from.screenY);
      ctx.lineTo(ex, ey);
      ctx.stroke();
      ctx.font = '12px Segoe UI';
      ctx.fillText(label, ex + 6, ey - 4);
    }}

    function drawMiniAxis(ctx, baseX, baseY, dx, dy, color, label) {{
      const zoomScale = Math.max(0.16, Math.min(0.5, state.transform.distance * 0.16));
      const manualScale = Math.max(0.5, Math.min(1.5, state.axisScalePercent / 100.0));
      const len = Math.max(1e-6, Math.sqrt(dx * dx + dy * dy));
      const ux = dx / len;
      const uy = dy / len;
      const pixelLength = Math.max(14, Math.min(44, 28 * zoomScale * manualScale * 3.0));
      const x = baseX + ux * pixelLength;
      const y = baseY + uy * pixelLength;
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = 2.2;
      ctx.beginPath();
      ctx.moveTo(baseX, baseY);
      ctx.lineTo(x, y);
      ctx.stroke();
      ctx.font = '12px Segoe UI';
      ctx.fillText(label, x + 5, y - 2);
    }}

    function faceCentroid(faceIndex) {{
      const tri = state.mesh.faces[faceIndex];
      if (!tri) return [0, 0, 0];
      const a = state.mesh.vertices[tri[0]];
      const b = state.mesh.vertices[tri[1]];
      const c = state.mesh.vertices[tri[2]];
      return [
        (a[0] + b[0] + c[0]) / 3.0,
        (a[1] + b[1] + c[1]) / 3.0,
        (a[2] + b[2] + c[2]) / 3.0
      ];
    }}

    function faceNormal(faceIndex) {{
      const tri = state.mesh.faces[faceIndex];
      if (!tri) return [0, 0, 1];
      const a = state.mesh.vertices[tri[0]];
      const b = state.mesh.vertices[tri[1]];
      const c = state.mesh.vertices[tri[2]];
      const ux = b[0] - a[0];
      const uy = b[1] - a[1];
      const uz = b[2] - a[2];
      const vx = c[0] - a[0];
      const vy = c[1] - a[1];
      const vz = c[2] - a[2];
      const nx = uy * vz - uz * vy;
      const ny = uz * vx - ux * vz;
      const nz = ux * vy - uy * vx;
      const len = Math.max(1e-9, Math.sqrt(nx * nx + ny * ny + nz * nz));
      return [nx / len, ny / len, nz / len];
    }}

    function dot3(a, b) {{
      return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    }}

    function computePivotForFaceIndices(faceIndices) {{
      if (!faceIndices.length) return [0, 0, 0];
      let sx = 0;
      let sy = 0;
      let sz = 0;
      for (const faceIndex of faceIndices) {{
        const c = faceCentroid(faceIndex);
        sx += c[0];
        sy += c[1];
        sz += c[2];
      }}
      const inv = 1.0 / faceIndices.length;
      return [sx * inv, sy * inv, sz * inv];
    }}

    function faceAreaMm2(faceIndex) {{
      const tri = state.mesh && state.mesh.faces ? state.mesh.faces[faceIndex] : null;
      if (!tri) return 0;
      const a = state.mesh.vertices[tri[0]];
      const b = state.mesh.vertices[tri[1]];
      const c = state.mesh.vertices[tri[2]];
      const ux = b[0] - a[0];
      const uy = b[1] - a[1];
      const uz = b[2] - a[2];
      const vx = c[0] - a[0];
      const vy = c[1] - a[1];
      const vz = c[2] - a[2];
      const cx = uy * vz - uz * vy;
      const cy = uz * vx - ux * vz;
      const cz = ux * vy - uy * vx;
      return 0.5 * Math.sqrt(cx * cx + cy * cy + cz * cz);
    }}

    function averageFaceNormal(faceIndices) {{
      if (!faceIndices || !faceIndices.length) return [0, 0, 1];
      let nx = 0;
      let ny = 0;
      let nz = 0;
      for (const faceIndex of faceIndices) {{
        const normal = faceNormal(faceIndex);
        const weight = Math.max(faceAreaMm2(faceIndex), 1e-9);
        nx += normal[0] * weight;
        ny += normal[1] * weight;
        nz += normal[2] * weight;
      }}
      const length = Math.sqrt(nx * nx + ny * ny + nz * nz);
      if (length <= 1e-9) return faceNormal(faceIndices[0]);
      return [nx / length, ny / length, nz / length];
    }}

    function rotateDirection(direction, rotationDeg) {{
      const rotated = rotatePoint(direction, [0, 0, 0], rotationDeg || {{ x: 0, y: 0, z: 0 }});
      const length = Math.max(1e-9, Math.sqrt(rotated[0] * rotated[0] + rotated[1] * rotated[1] + rotated[2] * rotated[2]));
      return [rotated[0] / length, rotated[1] / length, rotated[2] / length];
    }}

    function rotatePoint(point, pivot, rotationDeg) {{
      let x = point[0] - pivot[0];
      let y = point[1] - pivot[1];
      let z = point[2] - pivot[2];
      const rx = rotationDeg.x * Math.PI / 180.0;
      const ry = rotationDeg.y * Math.PI / 180.0;
      const rz = rotationDeg.z * Math.PI / 180.0;
      if (Math.abs(rx) > 1e-12) {{
        const cosX = Math.cos(rx);
        const sinX = Math.sin(rx);
        const nextY = y * cosX - z * sinX;
        const nextZ = y * sinX + z * cosX;
        y = nextY;
        z = nextZ;
      }}
      if (Math.abs(ry) > 1e-12) {{
        const cosY = Math.cos(ry);
        const sinY = Math.sin(ry);
        const nextX = x * cosY + z * sinY;
        const nextZ = -x * sinY + z * cosY;
        x = nextX;
        z = nextZ;
      }}
      if (Math.abs(rz) > 1e-12) {{
        const cosZ = Math.cos(rz);
        const sinZ = Math.sin(rz);
        const nextX = x * cosZ - y * sinZ;
        const nextY = x * sinZ + y * cosZ;
        x = nextX;
        y = nextY;
      }}
      return [x + pivot[0], y + pivot[1], z + pivot[2]];
    }}

    function transformPoint(point, pivot, moveVector, tiltVector) {{
      const tilt = tiltVector || state.gapTilt;
      const move = moveVector || state.gapMove;
      const rotated = rotatePoint(point, pivot, tilt);
      return [
        rotated[0] + move.x,
        rotated[1] + move.y,
        rotated[2] + move.z
      ];
    }}

    function getActivePreviewFaceIndices() {{
      if (!state.mesh) return [];
      if (state.gapTargetMode === 'component_move_gap') {{
        let faces = [];
        for (const rule of state.transformRules) {{
          if (!rule.enabled || rule.target_type !== 'component') continue;
          const objectId = rule.object_id;
          const object = state.objectsById.get(objectId);
          if (object && object.face_indices) {{
            faces = faces.concat(object.face_indices);
          }}
        }}
        return uniqueSorted(faces);
      }}
      return activeGapFaceIndices();
    }}

    function drawSingleTransformPreview(ctx, scene, faceIndices, move, tilt, colorFill, colorStroke) {{
      if (!faceIndices.length) return;
      const faces = state.mesh.faces;
      const verts = state.mesh.vertices;
      const pivot = computePivotForFaceIndices(faceIndices);
      for (const faceIndex of faceIndices) {{
        const tri = faces[faceIndex];
        if (!tri) continue;
        const p0 = scene.projectPoint(transformPoint(verts[tri[0]], pivot, move, tilt));
        const p1 = scene.projectPoint(transformPoint(verts[tri[1]], pivot, move, tilt));
        const p2 = scene.projectPoint(transformPoint(verts[tri[2]], pivot, move, tilt));
        ctx.beginPath();
        ctx.moveTo(p0.screenX, p0.screenY);
        ctx.lineTo(p1.screenX, p1.screenY);
        ctx.lineTo(p2.screenX, p2.screenY);
        ctx.closePath();
        ctx.fillStyle = colorFill;
        ctx.strokeStyle = colorStroke;
        ctx.lineWidth = 0.9;
        ctx.fill();
        ctx.stroke();
      }}
    }}

    function getCommittedTransformFaceSet() {{
      const committedFaces = new Set();
      for (const rule of state.transformRules) {{
        if (!rule.enabled || rule.target_type !== 'component' || !transformRuleHasAppliedTransform(rule)) continue;
        if (isComponentUnavailable(rule.object_id)) continue;
        const object = state.objectsById.get(rule.object_id);
        if (!object || !object.face_indices) continue;
        for (const faceIndex of object.face_indices) {{
          committedFaces.add(faceIndex);
        }}
      }}
      return committedFaces;
    }}

    function drawCommittedTransforms(ctx, scene) {{
      if (!scene) return;
      ctx.save();
      ctx.globalCompositeOperation = 'screen';
      for (const rule of state.transformRules) {{
        if (!rule.enabled || rule.target_type !== 'component' || !transformRuleHasAppliedTransform(rule)) continue;
        const object = state.objectsById.get(rule.object_id);
        if (!object || !object.face_indices || !object.face_indices.length) continue;
        const isActive = rule.rule_id === state.activeTransformRuleId;
        drawSingleTransformPreview(
          ctx,
          scene,
          object.face_indices,
          rule.move,
          rule.tilt,
          isActive ? 'rgba(239, 68, 68, 0.30)' : 'rgba(220, 38, 38, 0.22)',
          isActive ? 'rgba(248, 113, 113, 0.98)' : 'rgba(248, 113, 113, 0.88)'
        );
      }}
      ctx.restore();
    }}

    function drawDraftTransformPreview(ctx, scene) {{
      if (!scene || !state.previewOverlayEnabled) return;
      ctx.save();
      ctx.globalCompositeOperation = 'screen';
      if (state.gapTargetMode === 'component_move_gap') {{
        const rule = activeTransformRule();
        const object = rule ? state.objectsById.get(rule.object_id) : null;
        if (rule && !isComponentUnavailable(rule.object_id) && object && object.face_indices && object.face_indices.length && activeEditorDiffersFromRule()) {{
          drawSingleTransformPreview(
            ctx,
            scene,
            object.face_indices,
            state.gapMove,
            state.gapTilt,
            'rgba(250, 204, 21, 0.18)',
            'rgba(250, 204, 21, 0.96)'
          );
        }}
      }} else {{
        const faceIndices = visibleFaceIndices(getActivePreviewFaceIndices());
        if (faceIndices.length && (currentMoveMagnitude() > 1e-9 || currentTiltMagnitude() > 1e-9)) {{
          drawSingleTransformPreview(ctx, scene, faceIndices, state.gapMove, state.gapTilt, 'rgba(250, 204, 21, 0.18)', 'rgba(250, 204, 21, 0.95)');
        }}
      }}
      ctx.restore();
    }}

    function pointInTriangle(px, py, a, b, c) {{
      const area = (b.screenY - c.screenY) * (a.screenX - c.screenX) + (c.screenX - b.screenX) * (a.screenY - c.screenY);
      if (Math.abs(area) < 0.001) return false;
      const s = ((b.screenY - c.screenY) * (px - c.screenX) + (c.screenX - b.screenX) * (py - c.screenY)) / area;
      const t = ((c.screenY - a.screenY) * (px - c.screenX) + (a.screenX - c.screenX) * (py - c.screenY)) / area;
      const u = 1.0 - s - t;
      return s >= 0 && t >= 0 && u >= 0;
    }}

    function pickFaceFromCanvas(canvas, mode, clientX, clientY) {{
      const scene = mode === 'roi' ? state.renderScenes.roi : state.renderScenes.full;
      if (!scene) return null;
      const rect = canvas.getBoundingClientRect();
      const px = clientX - rect.left;
      const py = clientY - rect.top;
      for (let i = scene.triList.length - 1; i >= 0; i--) {{
        const tri = scene.triList[i];
        if (isFaceHidden(tri.idx)) continue;
        const isSelected = state.selectedFaces.has(tri.idx);
        if (mode === 'roi' && !isSelected) continue;
        if (pointInTriangle(px, py, tri.p0, tri.p1, tri.p2)) {{
          return tri.idx;
        }}
      }}
      return null;
    }}

    function currentMoveMagnitude() {{
      return vectorMagnitude(state.gapMove);
    }}

    function currentTiltMagnitude() {{
      return vectorMagnitude(state.gapTilt);
    }}

    function getSurfaceCluster(seedFaceIndex) {{
      if (seedFaceIndex === null || seedFaceIndex === undefined || !state.mesh) return [];
      if (isFaceHidden(seedFaceIndex)) return [];
      const visited = new Set();
      const queue = [seedFaceIndex];
      const seedNormal = faceNormal(seedFaceIndex);
      const cluster = [];
      while (queue.length) {{
        const faceIndex = queue.pop();
        if (visited.has(faceIndex)) continue;
        visited.add(faceIndex);
        if (isFaceHidden(faceIndex)) continue;
        const currentNormal = faceNormal(faceIndex);
        if (dot3(seedNormal, currentNormal) < 0.965) {{
          continue;
        }}
        cluster.push(faceIndex);
        const neighbors = state.faceAdjacency.get(faceIndex);
        if (!neighbors) continue;
        for (const neighbor of neighbors) {{
          if (!visited.has(neighbor)) {{
            queue.push(neighbor);
          }}
        }}
      }}
      return uniqueSorted(cluster);
    }}

    function setLocalGapFaces(faceIndices, additive) {{
      const next = additive ? new Set(state.localGapFaces) : new Set();
      for (const faceIndex of faceIndices) {{
        if (additive && next.has(faceIndex)) {{
          next.delete(faceIndex);
        }} else {{
          next.add(faceIndex);
        }}
      }}
      state.localGapFaces = new Set(uniqueSorted(Array.from(next)));
      updateGapSelectionStats();
      drawViewer();
    }}

    function selectLocalGapCluster(faceIndex, additive) {{
      if (faceIndex === null || faceIndex === undefined) return;
      const cluster = getSurfaceCluster(faceIndex);
      if (!cluster.length) return;
      setLocalGapFaces(cluster, additive);
    }}

    function selectLocalGapFacesInRect(canvas, mode, rect, additive) {{
      const scene = mode === 'roi' ? state.renderScenes.roi : state.renderScenes.full;
      if (!scene) return;
      const minX = Math.min(rect.x0, rect.x1);
      const maxX = Math.max(rect.x0, rect.x1);
      const minY = Math.min(rect.y0, rect.y1);
      const maxY = Math.max(rect.y0, rect.y1);
      const seedFaces = [];
      for (const tri of scene.triList) {{
        if (isFaceHidden(tri.idx)) continue;
        if (mode === 'roi' && !state.selectedFaces.has(tri.idx)) continue;
        const cx = (tri.p0.screenX + tri.p1.screenX + tri.p2.screenX) / 3.0;
        const cy = (tri.p0.screenY + tri.p1.screenY + tri.p2.screenY) / 3.0;
        if (cx >= minX && cx <= maxX && cy >= minY && cy <= maxY) {{
          seedFaces.push(tri.idx);
        }}
      }}
      if (!seedFaces.length) return;
      const collected = new Set();
      for (const faceIndex of seedFaces) {{
        for (const member of getSurfaceCluster(faceIndex)) {{
          if (!isFaceHidden(member)) collected.add(member);
        }}
      }}
      setLocalGapFaces(Array.from(collected), additive);
    }}

    function handleViewerPickFace(faceIndex, mode, pickEvent) {{
      const additive = !!(pickEvent && (pickEvent.ctrlKey || pickEvent.metaKey));
      if (state.emitterSelectionActive) {{
        handleEmitterGeometryPick(faceIndex, pickEvent || null);
      }} else if (state.receiverSelectionActive) {{
        handleReceiverGeometryPick(faceIndex, pickEvent || null);
      }} else if (state.gapTargetMode === 'face_gap') {{
        if (state.gapSelectionMethod === 'drag_box') {{
          setInspectedFace(faceIndex);
          return;
        }}
        setInspectedFace(faceIndex);
        selectLocalGapCluster(faceIndex, additive);
      }} else if (state.roiSelectionMode === 'click') {{
        toggleClickedFace(faceIndex);
      }} else {{
        setInspectedFace(faceIndex);
        if (state.gapTargetMode === 'component_move_gap') {{
          if (state.gapSelectionMethod === 'drag_box') {{
            return;
          }}
          const objectId = faceIndex === null || faceIndex === undefined ? null : state.faceToObjectId.get(faceIndex);
          setSelectedGapObject(objectId, null, additive);
        }}
      }}
    }}

    function setSelectedGapObjects(objectIds, additive, popupPosition) {{
      const next = additive ? new Set(state.selectedGapObjectIds) : new Set();
      for (const objectId of objectIds) {{
        if (!state.objectsById.has(objectId) || isComponentDeleted(objectId)) continue;
        if (additive && next.has(objectId)) {{
          next.delete(objectId);
        }} else {{
          next.add(objectId);
        }}
      }}
      const ordered = uniqueSorted(Array.from(next));
      state.selectedGapObjectIds = new Set(ordered);
      state.selectedGapObjectId = ordered.length ? ordered[0] : null;
      if (state.selectedGapObjectId !== null && state.selectedGapObjectId !== undefined && popupPosition) {{
        ensureTransformRuleForObject(state.selectedGapObjectId);
        renderTransformRules();
      }}
      syncComponentSelectionSummary();
      if (!ordered.length) {{
        hideMovePopup();
      }} else if (popupPosition) {{
        showMovePopupAt(popupPosition.clientX, popupPosition.clientY);
      }}
      const activeRule = activeTransformRule();
      if (activeRule) {{
        const isActiveStillSelected = ordered.includes(activeRule.object_id);
        if (!isActiveStillSelected) {{
          const nextRule = ordered.map(getTransformRuleByObjectId).find(Boolean);
          if (nextRule) {{
            state.activeTransformRuleId = nextRule.rule_id;
            syncEditorFromActiveRule();
          }}
        }}
      }}
      updateGapSelectionStats();
      drawViewer();
    }}

    function startTransformForObject(objectId, popupPosition) {{
      if (!state.objectsById.has(objectId) || isComponentDeleted(objectId)) return;
      state.gapTargetMode = 'component_move_gap';
      if (gapTargetMode) {{
        gapTargetMode.value = 'component_move_gap';
      }}
      setSelectedGapObject(objectId, popupPosition || null, false);
      ensureTransformRuleForObject(objectId);
      renderTransformRules();
      updateGapModeUI();
      updateGapSelectionStats();
      switchSideTab('transform_manager', {{ forceOpen: true }});
      if (popupPosition) {{
        showMovePopupAt(popupPosition.clientX, popupPosition.clientY);
      }} else {{
        showMovePopupAt(0, 0);
      }}
      drawViewer();
    }}

    function focusMaterialForObject(objectId, popupPosition) {{
      if (!state.objectsById.has(objectId) || isComponentDeleted(objectId)) return;
      ensureMaterialLibraryState();
      state.selectedMaterialObjectId = objectId;
      state.materialTargetMode = 'part';
      syncMaterialDraftFromObject(objectId);
      renderMaterialLibrary();
      updateMaterialTargetSummary();
      switchSideTab('material', {{ forceOpen: true }});
      if (popupPosition) {{
        showMaterialPopupAt(popupPosition.clientX, popupPosition.clientY);
      }} else {{
        showMaterialPopupAt(0, 0);
      }}
      drawViewer();
    }}

    function selectGapComponentsInRect(canvas, mode, rect, additive) {{
      const scene = mode === 'roi' ? state.renderScenes.roi : state.renderScenes.full;
      if (!scene) return;
      const minX = Math.min(rect.x0, rect.x1);
      const maxX = Math.max(rect.x0, rect.x1);
      const minY = Math.min(rect.y0, rect.y1);
      const maxY = Math.max(rect.y0, rect.y1);
      const objectIds = new Set();
      for (const tri of scene.triList) {{
        if (isFaceHidden(tri.idx)) continue;
        const cx = (tri.p0.screenX + tri.p1.screenX + tri.p2.screenX) / 3.0;
        const cy = (tri.p0.screenY + tri.p1.screenY + tri.p2.screenY) / 3.0;
        if (cx >= minX && cx <= maxX && cy >= minY && cy <= maxY) {{
          const objectId = state.faceToObjectId.get(tri.idx);
          if (objectId !== null && objectId !== undefined) {{
            objectIds.add(objectId);
          }}
        }}
      }}
      if (!objectIds.size) return;
      setSelectedGapObjects(Array.from(objectIds), additive);
    }}

    function addSelectedComponentsToManager() {{
      const ids = selectedComponentObjectIds();
      if (!ids.length) return;
      let firstAddedRuleId = null;
      for (const objectId of ids) {{
        let rule = getTransformRuleByObjectId(objectId);
        if (!rule) {{
          rule = buildTransformRule(objectId);
          if (!rule) continue;
          state.transformRules.push(rule);
          if (!firstAddedRuleId) {{
            firstAddedRuleId = rule.rule_id;
          }}
        }}
      }}
      if (firstAddedRuleId) {{
        state.activeTransformRuleId = firstAddedRuleId;
      }} else if (!state.activeTransformRuleId && state.transformRules.length) {{
        state.activeTransformRuleId = state.transformRules[0].rule_id;
      }}
      syncEditorFromActiveRule();
      renderTransformRules();
      updateGapSelectionStats();
      drawViewer();
    }}

    function checkedRulesOrActive() {{
      const checked = selectedTransformRuleIds().map(getTransformRuleById).filter(Boolean);
      if (checked.length) return checked;
      const active = activeTransformRule();
      return active ? [active] : [];
    }}

    function copyFromSelectedRules() {{
      const checked = selectedTransformRuleIds().map(getTransformRuleById).filter(Boolean);
      if (checked.length < 2) return;
      const source = checked[0];
      for (let index = 1; index < checked.length; index++) {{
        checked[index].move = {{ x: source.move.x, y: source.move.y, z: source.move.z }};
        checked[index].tilt = {{ x: source.tilt.x, y: source.tilt.y, z: source.tilt.z }};
      }}
      renderTransformRules();
      updateGapSelectionStats();
      drawViewer();
    }}

    function mirrorRules(axisKey) {{
      const rules = checkedRulesOrActive();
      for (const rule of rules) {{
        rule.move[axisKey] = -rule.move[axisKey];
      }}
      syncEditorFromActiveRule();
      renderTransformRules();
      updateGapSelectionStats();
      drawViewer();
    }}

    function resetSelectedRules() {{
      const rules = checkedRulesOrActive();
      for (const rule of rules) {{
        rule.move = {{ x: 0, y: 0, z: 0 }};
        rule.tilt = {{ x: 0, y: 0, z: 0 }};
      }}
      syncEditorFromActiveRule();
      renderTransformRules();
      updateGapSelectionStats();
      drawViewer();
    }}

    function applyEditorValuesToCheckedRules() {{
      const rules = checkedRulesOrActive();
      for (const rule of rules) {{
        rule.move = cloneVector(state.gapMove);
        rule.tilt = cloneVector(state.gapTilt);
      }}
      renderTransformRules();
      updateGapSelectionStats();
      drawViewer();
    }}

    function applyActiveTransformPreview() {{
      if (state.gapTargetMode !== 'component_move_gap') {{
        updateGapSelectionStats();
        drawViewer();
        return;
      }}
      let rule = activeTransformRule();
      if (!rule && state.selectedGapObjectId !== null && state.selectedGapObjectId !== undefined) {{
        rule = ensureTransformRuleForObject(state.selectedGapObjectId);
      }}
      if (!rule) return;
      rule.move = cloneVector(state.gapMove);
      rule.tilt = cloneVector(state.gapTilt);
      invalidateDirectRayTraceResult();
      renderTransformRules();
      updateGapSelectionStats();
      drawViewer();
    }}

    function resetTransformEditorInputs() {{
      state.gapMove = {{ x: 0, y: 0, z: 0 }};
      state.gapMoveText = {{ x: '0', y: '0', z: '0' }};
      state.gapTilt = {{ x: 0, y: 0, z: 0 }};
      state.gapTiltText = {{ x: '0', y: '0', z: '0' }};
      syncTransformInputs();
      updateGapSelectionStats();
      drawViewer();
    }}

    function restoreActiveTransformOriginal() {{
      if (state.gapTargetMode === 'component_move_gap') {{
        const rule = activeTransformRule();
        if (rule) {{
          rule.move = {{ x: 0, y: 0, z: 0 }};
          rule.tilt = {{ x: 0, y: 0, z: 0 }};
          invalidateDirectRayTraceResult();
          syncEditorFromActiveRule();
          renderTransformRules();
          updateGapSelectionStats();
          drawViewer();
          return;
        }}
      }}
      resetTransformEditorInputs();
    }}

    function activeGapFaceIndices() {{
      const manual = parseFaceList(gapFaceInput.value).filter(faceIndex => !isFaceDeleted(faceIndex));
      const merged = uniqueSorted(Array.from(state.localGapFaces).concat(manual).filter(faceIndex => !isFaceDeleted(faceIndex)));
      state.selectedGapFaces = new Set(merged);
      gapFaceIndices.value = merged.join(',');
      return merged;
    }}

    function gapTargetLabel() {{
      if (state.gapTargetMode === 'component_move_gap') {{
        const ids = uniqueSorted(Array.from(state.selectedGapObjectIds));
        if (!ids.length) return '선택된 부품 없음';
        if (ids.length === 1) {{
          const object = state.objectsById.get(ids[0]);
          return object ? object.object_name : '선택된 부품 없음';
        }}
        return '선택 부품 ' + ids.length + '개';
      }}
      const count = activeGapFaceIndices().length;
      return count > 0 ? ('선택 면 ' + count + '개') : '선택 면 없음';
    }}

    function setGapMoveVector(xValue, yValue, zValue) {{
      const rawX = String(xValue ?? '').trim();
      const rawY = String(yValue ?? '').trim();
      const rawZ = String(zValue ?? '').trim();
      state.gapMoveText = {{
        x: rawX,
        y: rawY,
        z: rawZ
      }};
      state.gapMove = {{
        x: parseMoveFieldValue(rawX),
        y: parseMoveFieldValue(rawY),
        z: parseMoveFieldValue(rawZ)
      }};
      syncTransformInputs();
      updateGapSelectionStats();
      drawViewer();
    }}

    function setGapTiltVector(xValue, yValue, zValue) {{
      const rawX = String(xValue ?? '').trim();
      const rawY = String(yValue ?? '').trim();
      const rawZ = String(zValue ?? '').trim();
      state.gapTiltText = {{
        x: rawX,
        y: rawY,
        z: rawZ
      }};
      state.gapTilt = {{
        x: parseMoveFieldValue(rawX),
        y: parseMoveFieldValue(rawY),
        z: parseMoveFieldValue(rawZ)
      }};
      syncTransformInputs();
      updateGapSelectionStats();
      drawViewer();
    }}

    function syncTransformInputs() {{
      if (gapMoveX) gapMoveX.value = state.gapMoveText.x;
      if (gapMoveY) gapMoveY.value = state.gapMoveText.y;
      if (gapMoveZ) gapMoveZ.value = state.gapMoveText.z;
      if (popupMoveX) popupMoveX.value = state.gapMoveText.x;
      if (popupMoveY) popupMoveY.value = state.gapMoveText.y;
      if (popupMoveZ) popupMoveZ.value = state.gapMoveText.z;
      if (cursorMoveX) cursorMoveX.value = state.gapMoveText.x;
      if (cursorMoveY) cursorMoveY.value = state.gapMoveText.y;
      if (cursorMoveZ) cursorMoveZ.value = state.gapMoveText.z;
      if (gapTiltX) gapTiltX.value = state.gapTiltText.x;
      if (gapTiltY) gapTiltY.value = state.gapTiltText.y;
      if (gapTiltZ) gapTiltZ.value = state.gapTiltText.z;
      if (popupTiltX) popupTiltX.value = state.gapTiltText.x;
      if (popupTiltY) popupTiltY.value = state.gapTiltText.y;
      if (popupTiltZ) popupTiltZ.value = state.gapTiltText.z;
      if (cursorTiltX) cursorTiltX.value = state.gapTiltText.x;
      if (cursorTiltY) cursorTiltY.value = state.gapTiltText.y;
      if (cursorTiltZ) cursorTiltZ.value = state.gapTiltText.z;
      gapMoveCombined.value = state.gapMove.x + ',' + state.gapMove.y + ',' + state.gapMove.z;
      gapTiltCombined.value = state.gapTilt.x + ',' + state.gapTilt.y + ',' + state.gapTilt.z;
    }}

    function estimatePreviewLiftMm(faceIndices, moveVector, tiltVector) {{
      if (!state.mesh || !faceIndices.length) return 0;
      const pivot = computePivotForFaceIndices(faceIndices);
      let maxLift = 0;
      for (const faceIndex of faceIndices) {{
        const point = faceCentroid(faceIndex);
        const transformed = transformPoint(point, pivot, moveVector, tiltVector);
        const dx = transformed[0] - point[0];
        const dy = transformed[1] - point[1];
        const dz = transformed[2] - point[2];
        const lift = Math.sqrt(dx * dx + dy * dy + dz * dz);
        if (lift > maxLift) maxLift = lift;
      }}
      return maxLift;
    }}

    function currentGapSummaryText() {{
      const targetName = gapTargetLabel();
      let previewMove = cloneVector(state.gapMove);
      let previewTilt = cloneVector(state.gapTilt);
      let appliedMove = cloneVector(state.gapMove);
      let appliedTilt = cloneVector(state.gapTilt);
      let moveMag = currentMoveMagnitude();
      let tiltMag = currentTiltMagnitude();
      let previewPending = false;
      if (state.gapTargetMode === 'component_move_gap') {{
        const activeRule = activeTransformRule();
        if (activeRule) {{
          appliedMove = cloneVector(activeRule.move);
          appliedTilt = cloneVector(activeRule.tilt);
          moveMag = vectorMagnitude(appliedMove);
          tiltMag = vectorMagnitude(appliedTilt);
          previewPending = !vectorEquals(previewMove, appliedMove) || !vectorEquals(previewTilt, appliedTilt);
        }}
      }}
      let faceIndices = getActivePreviewFaceIndices();
      if (state.gapTargetMode === 'component_move_gap') {{
        const activeRule = activeTransformRule();
        const activeObject = activeRule ? state.objectsById.get(activeRule.object_id) : null;
        if (activeObject && activeObject.face_indices) {{
          faceIndices = activeObject.face_indices;
        }}
      }}
      const previewLift = estimatePreviewLiftMm(faceIndices, previewMove, previewTilt);
      if (state.gapTargetMode === 'component_move_gap') {{
        return '대상: ' + targetName + '\\n'
          + 'Applied: X ' + appliedMove.x.toFixed(3) + ' / Y ' + appliedMove.y.toFixed(3) + ' / Z ' + appliedMove.z.toFixed(3) + ' mm\\n'
          + 'Applied Tilt: Rx ' + appliedTilt.x.toFixed(3) + ' / Ry ' + appliedTilt.y.toFixed(3) + ' / Rz ' + appliedTilt.z.toFixed(3) + ' deg\\n'
          + 'Preview: X ' + previewMove.x.toFixed(3) + ' / Y ' + previewMove.y.toFixed(3) + ' / Z ' + previewMove.z.toFixed(3) + ' mm\\n'
          + 'Preview Tilt: Rx ' + previewTilt.x.toFixed(3) + ' / Ry ' + previewTilt.y.toFixed(3) + ' / Rz ' + previewTilt.z.toFixed(3) + ' deg\\n'
          + '상태: ' + (previewPending ? 'Preview only (Apply 전)' : 'Applied 상태와 동일') + '\\n'
          + '|Applied Move|: ' + moveMag.toFixed(3) + ' mm\\n'
          + '|Applied Tilt|: ' + tiltMag.toFixed(3) + ' deg\\n'
          + '예상 최대 상대 이격(Preview 기준): ' + previewLift.toFixed(3) + ' mm';
      }}
      return '대상: ' + targetName + '\\n'
        + 'Preview: X ' + previewMove.x.toFixed(3) + ' / Y ' + previewMove.y.toFixed(3) + ' / Z ' + previewMove.z.toFixed(3) + ' mm\\n'
        + 'Preview Tilt: Rx ' + previewTilt.x.toFixed(3) + ' / Ry ' + previewTilt.y.toFixed(3) + ' / Rz ' + previewTilt.z.toFixed(3) + ' deg\\n'
        + '|Move|: ' + moveMag.toFixed(3) + ' mm\\n'
        + '|Tilt|: ' + tiltMag.toFixed(3) + ' deg\\n'
        + '예상 최대 상대 이격: ' + previewLift.toFixed(3) + ' mm';
    }}

    function updateGapFaceSummary() {{
      const activeFaces = activeGapFaceIndices();
      if (!activeFaces.length) {{
        gapFaceSummary.textContent = '아직 local face target이 선택되지 않았습니다. ROI 없이도 3D viewer에서 바로 선택할 수 있습니다.';
        return;
      }}
      const preview = activeFaces.slice(0, 12).join(', ');
      const suffix = activeFaces.length > 12 ? ' ...' : '';
      gapFaceSummary.textContent = 'Local face target: ' + activeFaces.length + ' faces / ' + preview + suffix;
    }}

    function updateGapModeUI() {{
      gapMode.value = state.gapTargetMode;
      const isComponentMode = state.gapTargetMode === 'component_move_gap';
      transformRulePanel.classList.toggle('hidden-block', !isComponentMode);
      gapFacePanel.classList.toggle('hidden-block', isComponentMode);
      gapModeHint.textContent = isComponentMode
        ? '부품 전체를 rigid move/tilt로 gap 발생 대상으로 사용합니다. 클릭 또는 드래그 박스로 부품을 선택하세요.'
        : 'ROI와 별개로 전체 모델에서 local face cluster를 선택해 move/tilt를 적용합니다.';
      viewerMovePanel.classList.toggle('hidden-block', !isComponentMode || state.selectedGapObjectIds.size === 0);
      if (!isComponentMode) {{
        hideMovePopup();
      }}
      updateGapFaceSummary();
      updateSelectionModeUI();
    }}

    function updateGapSelectionStats() {{
      const ids = uniqueSorted(Array.from(state.selectedGapObjectIds));
      const object = state.selectedGapObjectId !== null ? state.objectsById.get(state.selectedGapObjectId) : null;
      const isComponentMode = state.gapTargetMode === 'component_move_gap';
      syncComponentSelectionSummary();
      gapComponentIds.value = isComponentMode ? ids.join(',') : '';
      gapFaceIndices.value = isComponentMode ? '' : activeGapFaceIndices().join(',');
      const chipText = isComponentMode
        ? (ids.length > 1 ? ('Parts ' + ids.length) : (object ? object.object_name : 'No object'))
        : 'Local face move';
      viewerMoveChip.textContent = chipText;
      viewerMoveName.textContent = isComponentMode
        ? (ids.length > 1
          ? ('선택 부품 ' + ids.length + '개 / 현재 active rule 기준 preview 후 Apply')
          : (object ? (object.object_name + ' / faces: ' + object.face_count + ' / preview 후 Apply') : '3D viewer에서 부품을 클릭하면 선택됩니다.'))
        : '3D viewer local face cluster 선택 기준 local move/tilt';
      cursorMoveName.textContent = isComponentMode
        ? (ids.length > 1
          ? ('선택 부품 ' + ids.length + '개 / active rule 편집중')
          : (object ? (object.object_name + ' / preview input, Apply로 확정') : '선택된 부품 없음'))
        : '선택 면만 이동 모드';
      updateGapFaceSummary();
      const summaryText = currentGapSummaryText();
      gapMoveSummary.textContent = summaryText;
      viewerMoveSummary.textContent = summaryText;
      cursorMoveSummary.textContent = summaryText;
      viewerMovePanel.classList.toggle('hidden-block', !isComponentMode || ids.length === 0);
      highlightGapObjectListSelection();
    }}

    function highlightGapObjectListSelection() {{
      if (!gapObjectList) return;
      const rows = gapObjectList.querySelectorAll('[data-component-row-id]');
      for (const row of rows) {{
        const id = parseInt(row.getAttribute('data-component-row-id'), 10);
        row.classList.toggle('is-selected', state.selectedGapObjectIds.has(id));
      }}
    }}

    function setSelectedGapObject(objectId, popupPosition, additive) {{
      if (state.gapTargetMode !== 'component_move_gap') {{
        return;
      }}
      if (objectId === null || objectId === undefined || !state.objectsById.has(objectId)) {{
        state.selectedGapObjectId = null;
        state.selectedGapObjectIds = new Set();
        state.movePopupVisible = false;
        cursorMovePopup.classList.add('hidden-block');
        updateGapSelectionStats();
        drawViewer();
        return;
      }}
      setSelectedGapObjects([objectId], !!additive, popupPosition);
    }}

    function clampPopupPosition(left, top) {{
      const rect = viewerWrap.getBoundingClientRect();
      const popupWidth = cursorMovePopup.offsetWidth || 286;
      const popupHeight = cursorMovePopup.offsetHeight || 352;
      const maxLeft = Math.max(14, rect.width - popupWidth - 14);
      const maxTop = Math.max(24, rect.height - popupHeight - 14);
      return {{
        left: Math.min(maxLeft, Math.max(14, left)),
        top: Math.min(maxTop, Math.max(24, top)),
      }};
    }}

    function applyPopupPosition(left, top) {{
      const next = clampPopupPosition(left, top);
      cursorMovePopup.style.left = next.left + 'px';
      cursorMovePopup.style.top = next.top + 'px';
      state.popupPosition = next;
    }}

    function showMovePopupAt(clientX, clientY) {{
      cursorMovePopup.classList.remove('hidden-block');
      if (state.popupPosition) {{
        applyPopupPosition(state.popupPosition.left, state.popupPosition.top);
      }} else {{
        const rect = viewerWrap.getBoundingClientRect();
        const popupWidth = cursorMovePopup.offsetWidth || 286;
        const popupHeight = cursorMovePopup.offsetHeight || 352;
        const anchoredX = rect.width - popupWidth - 18;
        const anchoredYBase = viewerMovePanel.classList.contains('hidden-block')
          ? 96
          : (viewerMovePanel.offsetTop + viewerMovePanel.offsetHeight + 14);
        const x = Math.max(14, anchoredX);
        const y = Math.min(rect.height - popupHeight - 14, Math.max(anchoredYBase, 108));
        applyPopupPosition(x, y);
      }}
      state.movePopupVisible = true;
    }}

    function hideMovePopup() {{
      cursorMovePopup.classList.add('hidden-block');
      state.movePopupVisible = false;
    }}

    function startPopupDrag(ev) {{
      if (ev.target && ev.target.closest('#cursorMoveClose')) {{
        return;
      }}
      const popupRect = cursorMovePopup.getBoundingClientRect();
      state.popupDrag.active = true;
      state.popupDrag.offsetX = ev.clientX - popupRect.left;
      state.popupDrag.offsetY = ev.clientY - popupRect.top;
      cursorMovePopup.classList.add('is-dragging');
      ev.preventDefault();
    }}

    function movePopupDrag(ev) {{
      if (!state.popupDrag.active) return;
      const rect = viewerWrap.getBoundingClientRect();
      const left = ev.clientX - rect.left - state.popupDrag.offsetX;
      const top = ev.clientY - rect.top - state.popupDrag.offsetY;
      applyPopupPosition(left, top);
    }}

    function stopPopupDrag() {{
      if (!state.popupDrag.active) return;
      state.popupDrag.active = false;
      cursorMovePopup.classList.remove('is-dragging');
    }}

    function clampMaterialPopupPosition(left, top) {{
      const rect = viewerWrap.getBoundingClientRect();
      const popupWidth = cursorMaterialPopup.offsetWidth || 340;
      const popupHeight = cursorMaterialPopup.offsetHeight || 390;
      const maxLeft = Math.max(14, rect.width - popupWidth - 14);
      const maxTop = Math.max(24, rect.height - popupHeight - 14);
      return {{
        left: Math.min(maxLeft, Math.max(14, left)),
        top: Math.min(maxTop, Math.max(24, top)),
      }};
    }}

    function applyMaterialPopupPosition(left, top) {{
      const next = clampMaterialPopupPosition(left, top);
      cursorMaterialPopup.style.left = next.left + 'px';
      cursorMaterialPopup.style.top = next.top + 'px';
      state.materialPopupPosition = next;
    }}

    function showMaterialPopupAt(clientX, clientY) {{
      cursorMaterialPopup.classList.remove('hidden-block');
      if (state.materialPopupPosition) {{
        applyMaterialPopupPosition(state.materialPopupPosition.left, state.materialPopupPosition.top);
      }} else {{
        const rect = viewerWrap.getBoundingClientRect();
        const popupWidth = cursorMaterialPopup.offsetWidth || 340;
        const popupHeight = cursorMaterialPopup.offsetHeight || 390;
        const anchoredX = rect.width - popupWidth - 18;
        const transformBottom = cursorMovePopup.classList.contains('hidden-block')
          ? 88
          : ((parseFloat(cursorMovePopup.style.top || '108') || 108) + (cursorMovePopup.offsetHeight || 352) + 14);
        const x = Math.max(14, anchoredX);
        const y = Math.min(rect.height - popupHeight - 14, Math.max(transformBottom, 96));
        applyMaterialPopupPosition(x, y);
      }}
      updateMaterialTargetSummary();
    }}

    function hideMaterialPopup() {{
      cursorMaterialPopup.classList.add('hidden-block');
    }}

    function startMaterialPopupDrag(ev) {{
      if (ev.target && (ev.target.closest('#cursorMaterialClose') || ev.target.closest('#cursorMaterialOpenLibrary'))) {{
        return;
      }}
      const popupRect = cursorMaterialPopup.getBoundingClientRect();
      state.materialPopupDrag.active = true;
      state.materialPopupDrag.offsetX = ev.clientX - popupRect.left;
      state.materialPopupDrag.offsetY = ev.clientY - popupRect.top;
      cursorMaterialPopup.classList.add('is-dragging');
      ev.preventDefault();
    }}

    function moveMaterialPopupDrag(ev) {{
      if (!state.materialPopupDrag.active) return;
      const rect = viewerWrap.getBoundingClientRect();
      const left = ev.clientX - rect.left - state.materialPopupDrag.offsetX;
      const top = ev.clientY - rect.top - state.materialPopupDrag.offsetY;
      applyMaterialPopupPosition(left, top);
    }}

    function stopMaterialPopupDrag() {{
      if (!state.materialPopupDrag.active) return;
      state.materialPopupDrag.active = false;
      cursorMaterialPopup.classList.remove('is-dragging');
    }}

    function emitterById(emitterId) {{
      return state.emitters.find((item) => item.emitter_id === emitterId) || null;
    }}

    function currentEmitter() {{
      return state.activeEmitterId ? emitterById(state.activeEmitterId) : null;
    }}

    function emitterSpecPayload(emitter) {{
      return {{
        emitter_id: emitter.emitter_id,
        emitter_type: emitter.emitter_type || 'face',
        face_indices: uniqueSorted(emitter.face_indices || []),
        normal_mode: emitter.emitter_type === 'face' ? 'face_normal' : 'custom',
        normal_flip: !!emitter.normal_flip,
        custom_normal: emitter.emitter_type === 'face' ? null : (emitter.normal || null),
        direction_distribution: emitter.direction_distribution || 'lambertian',
        gaussian_sigma_deg: Number(emitter.gaussian_sigma_deg) || 12.0,
        power_mode: emitter.power_mode || 'total',
        power_lumen: Math.max(0, Number(emitter.power_lumen) || 0),
        power_density_lm_per_m2: Math.max(0, Number(emitter.power_density_lm_per_m2) || 0),
        center: emitter.center || null,
        u_axis: emitter.u_axis || null,
        v_axis: emitter.v_axis || null,
        width_mm: emitter.width_mm ?? null,
        height_mm: emitter.height_mm ?? null,
        reference_mode: emitter.reference_mode || null,
        surface_construction: emitter.surface_construction || 'rectangular_fit',
        polygon_vertices: Array.isArray(emitter.polygon_vertices) ? emitter.polygon_vertices : [],
        reference_vertex_indices: emitter.reference_vertex_indices || [],
        reference_edge_vertex_indices: emitter.reference_edge_vertex_indices || [],
        ray_count: Math.max(1, parseInt(emitter.ray_count, 10) || 10000),
        seed: null,
        enabled: emitter.enabled !== false
      }};
    }}

    function syncEmitterFormPayload() {{
      const payload = state.emitters.map(emitterSpecPayload);
      emitterSpecsJson.value = JSON.stringify(payload);
      const first = state.emitters.find((item) => item.enabled !== false) || null;
      const legacyStrength = document.querySelector('input[name="emitter_strength"]');
      if (!first) {{
        emitterType.value = '';
        emitterFace.value = '';
        emitterNormal.value = '';
        emitterDirectionDistribution.value = 'lambertian';
        if (legacyStrength) legacyStrength.value = '1.0';
        return;
      }}
      const firstIsFace = first.emitter_type === 'face';
      const normal = firstIsFace ? averageFaceNormal(first.face_indices || []) : (first.normal || [0, 0, 1]);
      const direction = first.normal_flip ? normal.map((value) => -value) : normal;
      emitterType.value = firstIsFace ? 'face' : '';
      emitterFace.value = firstIsFace ? String((first.face_indices || [])[0] ?? '') : '';
      emitterNormal.value = direction.map((value) => value.toFixed(6)).join(',');
      emitterDirectionDistribution.value = first.direction_distribution || 'lambertian';
      if (legacyStrength) legacyStrength.value = String(emitterEffectivePowerLm(first));
    }}

    function emitterTypeLabel(emitterTypeValue) {{
      if (emitterTypeValue === 'datum_plane') return 'Datum plane';
      if (emitterTypeValue === 'reference_plane') return 'Reference geometry';
      return 'CAD surface';
    }}

    function emitterAreaMm2(emitter) {{
      if (!emitter) return 0;
      if ((emitter.emitter_type || 'face') === 'face') {{
        return (emitter.face_indices || []).reduce((sum, faceIndex) => sum + faceAreaMm2(faceIndex), 0);
      }}
      if ((emitter.surface_construction || 'rectangular_fit') === 'polygon_auto') {{
        return Math.max(0, Number(emitter.polygon_area_mm2) || polygonAreaMm2FromPoints(emitter.polygon_vertices || []));
      }}
      return Math.max(0, Number(emitter.width_mm) || 0) * Math.max(0, Number(emitter.height_mm) || 0);
    }}

    function emitterEffectivePowerLm(emitter) {{
      if (!emitter) return 0;
      if ((emitter.power_mode || 'total') === 'power_per_area') {{
        return Math.max(0, Number(emitter.power_density_lm_per_m2) || 0) * emitterAreaMm2(emitter) * 1e-6;
      }}
      return Math.max(0, Number(emitter.power_lumen) || 0);
    }}

    function emitterPowerLabel(emitter) {{
      if ((emitter.power_mode || 'total') === 'power_per_area') {{
        return String(emitter.power_density_lm_per_m2) + ' lm/m²';
      }}
      return String(emitter.power_lumen) + ' lm';
    }}

    function renderEmitterList() {{
      emitterList.innerHTML = '';
      emitterEmpty.style.display = state.emitters.length ? 'none' : 'block';
      for (const emitter of state.emitters) {{
        const row = document.createElement('div');
        row.className = 'emitter-list-row' + (emitter.emitter_id === state.activeEmitterId ? ' active' : '');
        row.setAttribute('data-emitter-id', emitter.emitter_id);
        const dot = document.createElement('span');
        dot.className = 'emitter-list-dot';
        const body = document.createElement('div');
        const name = document.createElement('div');
        name.className = 'emitter-list-name';
        name.textContent = emitter.display_name || emitter.emitter_id;
        const meta = document.createElement('div');
        meta.className = 'emitter-list-meta';
        const geometryMeta = (emitter.emitter_type || 'face') === 'face'
          ? ((emitter.face_indices || []).length + ' faces')
          : ((emitter.surface_construction || 'rectangular_fit') === 'polygon_auto'
            ? ((emitter.polygon_vertices || []).length + '-vertex polygon · ' + emitterAreaMm2(emitter).toFixed(2) + ' mm²')
            : ((Number(emitter.width_mm) || 0).toFixed(2) + ' × ' + (Number(emitter.height_mm) || 0).toFixed(2) + ' mm'));
        meta.textContent = emitterTypeLabel(emitter.emitter_type) + ' · ' + geometryMeta + ' · ' + (emitter.direction_distribution || 'lambertian');
        body.appendChild(name);
        body.appendChild(meta);
        const power = document.createElement('span');
        power.className = 'emitter-list-power';
        power.textContent = emitterPowerLabel(emitter);
        const actions = document.createElement('div');
        actions.className = 'tree-row-actions';
        const settingsButton = document.createElement('button');
        settingsButton.type = 'button';
        settingsButton.className = 'tree-action-btn';
        settingsButton.textContent = 'Settings';
        settingsButton.addEventListener('click', function (ev) {{
          editEmitter(emitter.emitter_id, ev);
        }});
        const deleteButton = document.createElement('button');
        deleteButton.type = 'button';
        deleteButton.className = 'tree-action-btn delete';
        deleteButton.textContent = 'Delete';
        deleteButton.addEventListener('click', function (ev) {{
          ev.stopPropagation();
          deleteEmitterById(emitter.emitter_id);
        }});
        actions.appendChild(settingsButton);
        actions.appendChild(deleteButton);
        row.appendChild(dot);
        row.appendChild(body);
        row.appendChild(power);
        row.appendChild(actions);
        emitterList.appendChild(row);
      }}
      syncEmitterFormPayload();
      updateEmitterSelectionUI();
      updateRayTraceRunState();
    }}

    function updateEmitterDistributionUI() {{
      const isGaussian = emitterDistributionSelect.value === 'gaussian';
      emitterGaussianSigmaLabel.classList.toggle('hidden-block', !isGaussian);
    }}

    function updateEmitterPowerUI() {{
      const densityMode = emitterPowerModeSelect.value === 'power_per_area';
      emitterTotalPowerLabel.classList.toggle('hidden-block', densityMode);
      emitterPowerDensityLabel.classList.toggle('hidden-block', !densityMode);
    }}

    function updateEmitterGeometryUI() {{
      const emitterTypeValue = state.emitterDraftType || 'face';
      emitterDatumSection.classList.toggle('hidden-block', emitterTypeValue !== 'datum_plane');
      emitterReferenceSection.classList.toggle('hidden-block', emitterTypeValue !== 'reference_plane');
      emitterReselectFacesBtn.disabled = emitterTypeValue === 'datum_plane';
      emitterReselectFacesBtn.textContent = emitterTypeValue === 'datum_plane' ? 'Numeric geometry' : 'Select geometry';
      if (emitterTypeValue === 'reference_plane') {{
        const isEdges = emitterReferenceModeSelect.value === 'two_edges';
        emitterReferenceSurfaceWrap.classList.toggle('hidden-block', isEdges);
        if (isEdges) emitterReferenceSurfaceSelect.value = 'rectangular_fit';
        const selectedCount = isEdges ? state.emitterReferenceEdges.length : state.emitterReferenceVertices.length;
        emitterReferenceHint.textContent = isEdges
          ? '서로 다른 edge 2개를 선택합니다. 잘못 선택했으면 아래 Clear selected edges를 누르세요.'
          : (emitterReferenceSurfaceSelect.value === 'polygon_auto'
            ? '선택 순서와 무관하게 점을 평면에 투영한 뒤 외곽점을 자동 폐곡선으로 연결합니다. 내부점은 꼭지점에서 제외될 수 있습니다.'
            : '선택한 vertex를 모두 포함하는 사각 평면을 생성합니다. 최대 6개까지 선택하며 같은 점을 다시 누르면 제외됩니다.');
        emitterClearReferencesBtn.textContent = isEdges ? 'Clear selected edges' : 'Clear selected points';
        emitterClearReferencesBtn.disabled = selectedCount === 0;
        emitterReferenceCount.textContent = isEdges ? (selectedCount + ' / 2 selected') : (selectedCount + ' / 6 selected · minimum 3');
      }}
    }}

    function updateEmitterDraftSummary() {{
      const emitterTypeValue = state.emitterDraftType || 'face';
      let count = 0;
      let area = 0;
      let normal = [0, 0, 1];
      let summary = '';
      if (emitterTypeValue === 'face') {{
        const faces = uniqueSorted(Array.from(state.emitterDraftFaces));
        count = faces.length;
        normal = averageFaceNormal(faces);
        area = faces.reduce((sum, faceIndex) => sum + faceAreaMm2(faceIndex), 0);
        summary = faces.length
          ? ('Type: CAD surface\\nFaces: ' + faces.join(', ') + '\\nArea: ' + area.toFixed(3) + ' mm²')
          : 'Type: CAD surface\\n선택 면 없음';
        cursorEmitterNameHint.textContent = faces.length
          ? '선택 surface가 주황색으로 표시됩니다. 화살표가 광 방출 normal입니다.'
          : '3D viewer에서 실제 CAD surface를 선택하세요.';
      }} else {{
        const plane = currentDraftEmitterPlane();
        normal = plane && plane.normal ? plane.normal : [0, 0, 1];
        if (emitterTypeValue === 'datum_plane') {{
          count = plane && plane.center ? 1 : 0;
          area = plane && plane.center ? plane.widthMm * plane.heightMm : 0;
          emitterAreaInput.value = area.toFixed(3);
          summary = plane && plane.center
            ? ('Type: Datum plane\\nCenter: (' + plane.center.map((value) => value.toFixed(3)).join(', ') + ') mm\\nSize: ' + plane.widthMm.toFixed(3) + ' × ' + plane.heightMm.toFixed(3) + ' mm')
            : 'Type: Datum plane\\n형상 입력 필요';
          cursorEmitterNameHint.textContent = '좌표·크기·회전각으로 빈 공간의 가상 발광면을 정의합니다.';
        }} else {{
          const mode = emitterReferenceModeSelect.value || 'three_vertices';
          count = mode === 'two_edges' ? state.emitterReferenceEdges.length : state.emitterReferenceVertices.length;
          const polygonMode = mode !== 'two_edges' && emitterReferenceSurfaceSelect.value === 'polygon_auto';
          area = plane && plane.center
            ? (polygonMode ? Math.max(0, Number(plane.polygonAreaMm2) || 0) : plane.widthMm * plane.heightMm)
            : 0;
          summary = 'Type: Reference geometry\\n' + (mode === 'two_edges' ? 'Edges: ' + count + ' / 2' : 'Vertices: ' + count + ' / 3–6');
          if (plane && plane.center && polygonMode) {{
            summary += '\\nConstruction: Polygon – Auto closed boundary';
            summary += '\\nBoundary vertices: ' + String(plane.boundaryVertexCount || 0);
            if (Number(plane.interiorPointCount) > 0) summary += '\\nInterior points excluded from boundary: ' + String(plane.interiorPointCount);
          }} else if (plane && plane.center) {{
            summary += '\\nConstruction: Plane containing vertices';
            summary += '\\nSize: ' + plane.widthMm.toFixed(3) + ' × ' + plane.heightMm.toFixed(3) + ' mm';
          }}
          if (plane && Number(plane.planarityErrorMm) > 0.001) summary += '\\nPlanarity deviation: ' + plane.planarityErrorMm.toFixed(4) + ' mm';
          if (plane && Number(plane.planarityErrorMm) > REFERENCE_PLANARITY_TOLERANCE_MM) summary += '\\nWARNING: exceeds 0.05 mm tolerance';
          cursorEmitterNameHint.textContent = plane && plane.center
            ? '선택한 CAD reference 사이에 가상 발광면이 생성되었습니다.'
            : (mode === 'two_edges' ? '3D viewer에서 기준 edge 2개를 선택하세요.' : '서로 일직선이 아닌 vertex를 최소 3개 선택하세요.');
        }}
      }}
      const shownNormal = emitterNormalFlipInput.checked ? normal.map((value) => -value) : normal;
      emitterFaceCountInput.value = String(count);
      emitterGeometrySummary.textContent = summary + '\\nArea: ' + area.toFixed(3) + ' mm²\\nNormal: (' + shownNormal.map((value) => value.toFixed(4)).join(', ') + ')';
      cursorEmitterChip.textContent = currentEmitter() ? 'Edit ' + emitterTypeLabel(emitterTypeValue) : 'New ' + emitterTypeLabel(emitterTypeValue);
      updateEmitterPowerUI();
      updateEmitterGeometryUI();
    }}

    function updateEmitterSelectionUI() {{
      const emitterTypeValue = state.emitterDraftType || 'face';
      const isFaceSelecting = state.emitterSelectionActive && emitterTypeValue === 'face';
      const isReferenceSelecting = state.emitterSelectionActive && emitterTypeValue === 'reference_plane';
      addFaceEmitterBtn.textContent = isFaceSelecting ? 'Selecting CAD surfaces…' : '+ CAD surface emitter';
      addDatumEmitterBtn.textContent = '+ Datum plane emitter';
      addReferenceEmitterBtn.textContent = isReferenceSelecting ? 'Selecting references…' : '+ Reference geometry emitter';
      addFaceEmitterBtn.disabled = state.emitterSelectionActive;
      addDatumEmitterBtn.disabled = state.emitterSelectionActive;
      addReferenceEmitterBtn.disabled = state.emitterSelectionActive;
      emitterSelectionBanner.classList.toggle('active', state.emitterSelectionActive);
      if (isFaceSelecting) {{
        const faceCount = state.emitterDraftFaces.size;
        emitterSelectionBanner.textContent = faceCount
          ? ('선택 중: ' + faceCount + ' surfaces · Ctrl+클릭으로 추가/제외 · Apply로 확정')
          : '3D viewer에서 실제 방출 surface를 클릭하세요. Ctrl+클릭으로 여러 surface를 묶을 수 있습니다.';
        viewerTip.textContent = 'Emitter: Click CAD surface, Ctrl+click = add/remove, Apply = save.';
      }} else if (isReferenceSelecting) {{
        const isEdges = emitterReferenceModeSelect.value === 'two_edges';
        const selectedCount = isEdges ? state.emitterReferenceEdges.length : state.emitterReferenceVertices.length;
        emitterSelectionBanner.textContent = isEdges
          ? ('Reference edge 선택: ' + selectedCount + ' / 2 · Apply로 확정')
          : ('Reference vertex 선택: ' + selectedCount + ' / 3–6 · 최소 3개 선택 후 Apply');
        viewerTip.textContent = isEdges ? 'Emitter: Click near two CAD edges.' : 'Emitter: Select 3 to 6 CAD vertices. Click again to remove.';
      }} else if (state.emitters.length) {{
        emitterSelectionBanner.textContent = state.emitters.length + ' emitter(s) registered. List의 Settings에서 편집합니다.';
        viewerTip.textContent = 'Drag = rotate, Middle drag = rotate, Wheel = zoom, Right click = component menu, Right drag = pan, Shift/Alt+drag = roll.';
      }} else {{
        emitterSelectionBanner.textContent = '광원이 없습니다. Add에서 생성 방식을 선택하세요.';
        viewerTip.textContent = 'Drag = rotate, Middle drag = rotate, Wheel = zoom, Right click = component menu, Right drag = pan, Shift/Alt+drag = roll.';
      }}
    }}

    function resetEmitterEditorValues() {{
      const emitter = currentEmitter();
      const center = emitter && emitter.center ? emitter.center : modelCenterMm();
      const rotation = emitter && emitter.rotation_deg ? emitter.rotation_deg : {{ x: 0, y: 0, z: 0 }};
      emitterNameInput.value = emitter ? emitter.display_name : ('Emitter ' + state.emitterSequence);
      emitterPowerModeSelect.value = emitter ? (emitter.power_mode || 'total') : 'total';
      emitterPowerInput.value = emitter ? String(emitter.power_lumen ?? 1.0) : '1.0';
      emitterPowerDensityInput.value = emitter ? String(emitter.power_density_lm_per_m2 ?? 100) : '100';
      emitterRayCountInput.value = emitter ? String(emitter.ray_count) : '10000';
      emitterDistributionSelect.value = emitter ? emitter.direction_distribution : 'lambertian';
      emitterGaussianSigmaInput.value = emitter ? String(emitter.gaussian_sigma_deg) : '12.0';
      emitterNormalFlipInput.checked = emitter ? !!emitter.normal_flip : false;
      emitterCenterX.value = String(center[0] ?? 0);
      emitterCenterY.value = String(center[1] ?? 0);
      emitterCenterZ.value = String(center[2] ?? 0);
      emitterWidthInput.value = emitter ? String(emitter.width_mm ?? 20) : '20';
      emitterHeightInput.value = emitter ? String(emitter.height_mm ?? 20) : '20';
      emitterRotationX.value = String(rotation.x ?? 0);
      emitterRotationY.value = String(rotation.y ?? 0);
      emitterRotationZ.value = String(rotation.z ?? 0);
      emitterReferenceModeSelect.value = emitter ? (emitter.reference_mode || 'three_vertices') : 'three_vertices';
      emitterReferenceSurfaceSelect.value = emitter ? (emitter.surface_construction || 'rectangular_fit') : 'rectangular_fit';
      emitterDeleteBtn.disabled = false;
      emitterDeleteBtn.textContent = emitter ? 'Delete emitter' : 'Discard draft';
      updateEmitterDistributionUI();
      updateEmitterPowerUI();
      updateEmitterGeometryUI();
      updateEmitterDraftSummary();
    }}

    function clampEmitterPopupPosition(left, top) {{
      const rect = viewerWrap.getBoundingClientRect();
      const popupWidth = cursorEmitterPopup.offsetWidth || 382;
      const popupHeight = cursorEmitterPopup.offsetHeight || 620;
      return {{
        left: Math.min(Math.max(14, rect.width - popupWidth - 14), Math.max(14, left)),
        top: Math.min(Math.max(24, rect.height - popupHeight - 14), Math.max(24, top))
      }};
    }}

    function applyEmitterPopupPosition(left, top) {{
      const next = clampEmitterPopupPosition(left, top);
      cursorEmitterPopup.style.left = next.left + 'px';
      cursorEmitterPopup.style.top = next.top + 'px';
      state.emitterPopupPosition = next;
    }}

    function showEmitterPopupAt(clientX, clientY) {{
      cursorEmitterPopup.classList.remove('hidden-block');
      if (clientX !== null && clientX !== undefined && clientY !== null && clientY !== undefined) {{
        const rect = viewerWrap.getBoundingClientRect();
        applyEmitterPopupPosition(clientX - rect.left + 14, clientY - rect.top + 14);
      }} else if (state.emitterPopupPosition) {{
        applyEmitterPopupPosition(state.emitterPopupPosition.left, state.emitterPopupPosition.top);
      }} else {{
        const rect = viewerWrap.getBoundingClientRect();
        applyEmitterPopupPosition(rect.width - (cursorEmitterPopup.offsetWidth || 382) - 18, 42);
      }}
    }}

    function hideEmitterPopup() {{
      cursorEmitterPopup.classList.add('hidden-block');
    }}

    function startEmitterPopupDrag(ev) {{
      if (ev.target && ev.target.closest('button')) return;
      const popupRect = cursorEmitterPopup.getBoundingClientRect();
      state.emitterPopupDrag.active = true;
      state.emitterPopupDrag.offsetX = ev.clientX - popupRect.left;
      state.emitterPopupDrag.offsetY = ev.clientY - popupRect.top;
      cursorEmitterPopup.classList.add('is-dragging');
      ev.preventDefault();
    }}

    function moveEmitterPopupDrag(ev) {{
      if (!state.emitterPopupDrag.active) return;
      const rect = viewerWrap.getBoundingClientRect();
      applyEmitterPopupPosition(
        ev.clientX - rect.left - state.emitterPopupDrag.offsetX,
        ev.clientY - rect.top - state.emitterPopupDrag.offsetY
      );
    }}

    function stopEmitterPopupDrag() {{
      if (!state.emitterPopupDrag.active) return;
      state.emitterPopupDrag.active = false;
      cursorEmitterPopup.classList.remove('is-dragging');
    }}

    function beginEmitterCreation(emitterTypeValue) {{
      state.receiverSelectionActive = false;
      hideReceiverPopup();
      updateReceiverSelectionUI();
      state.activeEmitterId = null;
      state.emitterDraftType = emitterTypeValue;
      state.emitterDraftFaces = new Set();
      state.emitterReferenceVertices = [];
      state.emitterReferenceEdges = [];
      state.emitterSelectionActive = emitterTypeValue !== 'datum_plane';
      resetEmitterEditorValues();
      switchSideTab('raytracing', {{ forceOpen: true }});
      showEmitterPopupAt(null, null);
      renderEmitterList();
      drawViewer();
    }}

    function beginFaceEmitterSelection() {{ beginEmitterCreation('face'); }}
    function beginDatumEmitterCreation() {{ beginEmitterCreation('datum_plane'); }}
    function beginReferenceEmitterSelection() {{ beginEmitterCreation('reference_plane'); }}

    function editEmitter(emitterId, popupPosition) {{
      const emitter = emitterById(emitterId);
      if (!emitter) return;
      state.activeEmitterId = emitterId;
      state.emitterDraftType = emitter.emitter_type || 'face';
      state.emitterDraftFaces = new Set(emitter.face_indices || []);
      state.emitterReferenceVertices = Array.from(emitter.reference_vertex_indices || []).slice(0, 6);
      state.emitterReferenceEdges = (emitter.reference_edge_vertex_indices || []).map((edge) => Array.from(edge));
      state.emitterSelectionActive = false;
      resetEmitterEditorValues();
      showEmitterPopupAt(
        popupPosition && popupPosition.clientX !== undefined ? popupPosition.clientX : null,
        popupPosition && popupPosition.clientY !== undefined ? popupPosition.clientY : null
      );
      renderEmitterList();
      drawViewer();
    }}

    function reselectEmitterGeometry() {{
      if (state.emitterDraftType === 'datum_plane') return;
      state.emitterSelectionActive = true;
      updateEmitterSelectionUI();
      drawViewer();
    }}

    function cancelEmitterSelection() {{
      state.emitterSelectionActive = false;
      if (!currentEmitter()) {{
        state.emitterDraftFaces = new Set();
        state.emitterReferenceVertices = [];
        state.emitterReferenceEdges = [];
        hideEmitterPopup();
      }}
      updateEmitterSelectionUI();
      drawViewer();
    }}

    function clearEmitterReferenceSelection() {{
      state.emitterReferenceVertices = [];
      state.emitterReferenceEdges = [];
      state.emitterSelectionActive = state.emitterDraftType === 'reference_plane';
      updateEmitterDraftSummary();
      updateEmitterSelectionUI();
      drawViewer();
    }}

    function handleEmitterGeometryPick(faceIndex, pickEvent) {{
      if (faceIndex === null || faceIndex === undefined) return;
      if (state.emitterDraftType === 'reference_plane') {{
        let maximumReached = false;
        if ((emitterReferenceModeSelect.value || 'three_vertices') === 'two_edges') {{
          const edge = pickEvent && Array.isArray(pickEvent.edgeVertexIndices) ? pickEvent.edgeVertexIndices.map(Number).sort((a, b) => a - b) : [];
          if (edge.length !== 2 || edge.some((value) => !Number.isInteger(value))) return;
          const signature = edge.join(':');
          const existing = state.emitterReferenceEdges.findIndex((item) => item.slice().sort((a, b) => a - b).join(':') === signature);
          if (existing >= 0) state.emitterReferenceEdges.splice(existing, 1);
          else if (state.emitterReferenceEdges.length < 2) state.emitterReferenceEdges.push(edge);
          else state.emitterReferenceEdges = [state.emitterReferenceEdges[1], edge];
        }} else {{
          const vertexIndex = Number(pickEvent && pickEvent.vertexIndex);
          if (!Number.isInteger(vertexIndex) || vertexIndex < 0) return;
          const existing = state.emitterReferenceVertices.indexOf(vertexIndex);
          if (existing >= 0) state.emitterReferenceVertices.splice(existing, 1);
          else if (state.emitterReferenceVertices.length < 6) state.emitterReferenceVertices.push(vertexIndex);
          else maximumReached = true;
        }}
        updateEmitterDraftSummary();
        updateEmitterSelectionUI();
        if (maximumReached) {{
          emitterSelectionBanner.textContent = 'vertex는 최대 6개까지 선택할 수 있습니다. 다른 점으로 바꾸려면 기존 점을 다시 클릭하거나 Clear selected points를 누르세요.';
        }}
        if (cursorEmitterPopup.classList.contains('hidden-block')) {{
          showEmitterPopupAt(null, null);
        }}
        drawViewer();
        return;
      }}
      const additive = !!(pickEvent && (pickEvent.ctrlKey || pickEvent.metaKey));
      const cluster = getSurfaceCluster(faceIndex);
      const next = additive ? new Set(state.emitterDraftFaces) : new Set();
      const removeCluster = additive && cluster.every((member) => next.has(member));
      for (const member of cluster) {{
        if (removeCluster) next.delete(member);
        else next.add(member);
      }}
      state.emitterDraftFaces = new Set(uniqueSorted(Array.from(next)));
      updateEmitterDraftSummary();
      updateEmitterSelectionUI();
      showEmitterPopupAt(pickEvent?.clientX, pickEvent?.clientY);
      drawViewer();
    }}

    function applyEmitterEditor() {{
      const emitterTypeValue = state.emitterDraftType || 'face';
      const faces = uniqueSorted(Array.from(state.emitterDraftFaces));
      const plane = emitterTypeValue === 'face' ? null : currentDraftEmitterPlane();
      if (emitterTypeValue === 'face' && !faces.length) {{
        emitterSelectionBanner.textContent = '먼저 3D viewer에서 emitter 면을 선택하세요.';
        emitterSelectionBanner.classList.add('active');
        return;
      }}
      if (emitterTypeValue === 'reference_plane' && (!plane || !plane.center)) {{
        emitterSelectionBanner.textContent = emitterReferenceModeSelect.value === 'two_edges'
          ? 'Reference emitter를 만들려면 서로 다른 edge 2개를 선택하세요.'
          : 'Reference emitter를 만들려면 서로 일직선이 아닌 vertex를 3~6개 선택하세요.';
        emitterSelectionBanner.classList.add('active');
        return;
      }}
      if (
        emitterTypeValue === 'reference_plane'
        && emitterReferenceModeSelect.value !== 'two_edges'
        && emitterReferenceSurfaceSelect.value === 'polygon_auto'
      ) {{
        if (!plane.polygonPoints || plane.polygonPoints.length < 3 || Number(plane.polygonAreaMm2) <= 1e-9) {{
          emitterSelectionBanner.textContent = 'Polygon 경계를 만들 수 없습니다. 서로 일직선이 아닌 vertex를 3개 이상 선택하세요.';
          emitterSelectionBanner.classList.add('active');
          return;
        }}
        if (Number(plane.planarityErrorMm) > REFERENCE_PLANARITY_TOLERANCE_MM) {{
          emitterSelectionBanner.textContent = '선택점의 평면 오차가 0.05 mm를 초과합니다. 더 평탄한 vertex를 다시 선택하세요.';
          emitterSelectionBanner.classList.add('active');
          return;
        }}
      }}
      const power = Math.max(0, parseMoveFieldValue(emitterPowerInput.value));
      const powerDensity = Math.max(0, parseMoveFieldValue(emitterPowerDensityInput.value));
      const rayCount = Math.max(1, parseInt(emitterRayCountInput.value, 10) || 10000);
      const sigma = Math.max(0.1, parseFloat(emitterGaussianSigmaInput.value) || 12.0);
      let emitter = currentEmitter();
      if (!emitter) {{
        emitter = {{ emitter_id: 'emitter_' + String(state.emitterSequence).padStart(3, '0') }};
        state.emitterSequence += 1;
        state.emitters.push(emitter);
      }}
      emitter.display_name = emitterNameInput.value.trim() || ('Emitter ' + state.emitterSequence);
      emitter.emitter_type = emitterTypeValue;
      emitter.face_indices = emitterTypeValue === 'face' ? faces : [];
      emitter.normal_mode = emitterTypeValue === 'face' ? 'face_normal' : 'custom';
      emitter.normal_flip = !!emitterNormalFlipInput.checked;
      emitter.direction_distribution = emitterDistributionSelect.value || 'lambertian';
      emitter.gaussian_sigma_deg = sigma;
      emitter.power_mode = emitterPowerModeSelect.value || 'total';
      emitter.power_lumen = power;
      emitter.power_density_lm_per_m2 = powerDensity;
      emitter.ray_count = rayCount;
      emitter.enabled = true;
      emitter.center = plane && plane.center ? Array.from(plane.center) : null;
      emitter.u_axis = plane && plane.uAxis ? Array.from(plane.uAxis) : null;
      emitter.v_axis = plane && plane.vAxis ? Array.from(plane.vAxis) : null;
      emitter.normal = plane && plane.normal ? Array.from(plane.normal) : null;
      emitter.width_mm = plane && plane.widthMm ? plane.widthMm : null;
      emitter.height_mm = plane && plane.heightMm ? plane.heightMm : null;
      emitter.reference_mode = emitterTypeValue === 'reference_plane' ? (emitterReferenceModeSelect.value || 'three_vertices') : null;
      emitter.surface_construction = emitterTypeValue === 'reference_plane'
        ? (emitterReferenceModeSelect.value === 'two_edges' ? 'rectangular_fit' : (emitterReferenceSurfaceSelect.value || 'rectangular_fit'))
        : 'rectangular_fit';
      emitter.polygon_vertices = emitter.surface_construction === 'polygon_auto' && plane && plane.polygonPoints
        ? plane.polygonPoints.map((point) => Array.from(point))
        : [];
      emitter.polygon_area_mm2 = emitter.surface_construction === 'polygon_auto' && plane
        ? Math.max(0, Number(plane.polygonAreaMm2) || 0)
        : null;
      emitter.reference_vertex_indices = emitterTypeValue === 'reference_plane' ? Array.from(state.emitterReferenceVertices) : [];
      emitter.reference_edge_vertex_indices = emitterTypeValue === 'reference_plane' ? state.emitterReferenceEdges.map((edge) => Array.from(edge)) : [];
      emitter.rotation_deg = emitterTypeValue === 'datum_plane' ? {{
        x: parseMoveFieldValue(emitterRotationX.value),
        y: parseMoveFieldValue(emitterRotationY.value),
        z: parseMoveFieldValue(emitterRotationZ.value)
      }} : {{ x: 0, y: 0, z: 0 }};
      state.activeEmitterId = emitter.emitter_id;
      state.emitterSelectionActive = false;
      invalidateDirectRayTraceResult();
      resetEmitterEditorValues();
      renderEmitterList();
      drawViewer();
    }}

    function resetEmitterProperties() {{
      const savedEmitter = currentEmitter();
      const referenceMode = emitterReferenceModeSelect.value;
      const surfaceConstruction = emitterReferenceSurfaceSelect.value;
      resetEmitterEditorValues();
      emitterReferenceModeSelect.value = referenceMode;
      emitterReferenceSurfaceSelect.value = surfaceConstruction;
      updateEmitterGeometryUI();
      updateEmitterDraftSummary();
      emitterSelectionBanner.textContent = savedEmitter
        ? '저장된 emitter 속성값으로 되돌렸습니다. 선택 geometry는 유지됩니다.'
        : 'Emitter 속성값을 기본값으로 초기화했습니다. 선택 geometry는 유지됩니다.';
      emitterSelectionBanner.classList.add('active');
      drawViewer();
    }}

    function deleteCurrentEmitter() {{
      const deletedSavedEmitter = !!state.activeEmitterId;
      if (deletedSavedEmitter) state.emitters = state.emitters.filter((item) => item.emitter_id !== state.activeEmitterId);
      state.activeEmitterId = null;
      state.emitterDraftType = 'face';
      state.emitterDraftFaces = new Set();
      state.emitterReferenceVertices = [];
      state.emitterReferenceEdges = [];
      state.emitterSelectionActive = false;
      invalidateDirectRayTraceResult();
      hideEmitterPopup();
      renderEmitterList();
      drawViewer();
      emitterSelectionBanner.textContent = deletedSavedEmitter ? 'Emitter를 삭제했습니다.' : '생성 중인 emitter preview를 삭제했습니다.';
      emitterSelectionBanner.classList.add('active');
    }}

    function deleteEmitterById(emitterId) {{
      if (!emitterById(emitterId)) return;
      state.activeEmitterId = emitterId;
      deleteCurrentEmitter();
    }}

    function resetEmittersForScene() {{
      state.emitters = [];
      state.activeEmitterId = null;
      state.emitterDraftType = 'face';
      state.emitterDraftFaces = new Set();
      state.emitterReferenceVertices = [];
      state.emitterReferenceEdges = [];
      state.emitterSelectionActive = false;
      state.emitterSequence = 1;
      hideEmitterPopup();
      renderEmitterList();
    }}

    function receiverPlacementLabel(placementMode) {{
      if (placementMode === 'reference_plane') return 'Reference geometry';
      if (placementMode === 'current_view') return 'Current view';
      return 'Datum plane';
    }}

    function receiverSpecPayload(receiver) {{
      const baseNormal = normalizeArray3(receiver.normal || [0, 0, 1]) || [0, 0, 1];
      const effectiveNormal = receiver.normal_flip ? baseNormal.map((value) => -value) : baseNormal;
      return {{
        receiver_id: receiver.receiver_id,
        receiver_type: 'rectangle',
        display_name: receiver.display_name || receiver.receiver_id,
        placement_mode: receiver.placement_mode || 'datum_plane',
        center: receiver.center || [0, 0, 0],
        normal: effectiveNormal,
        u_axis: receiver.u_axis || [1, 0, 0],
        v_axis: receiver.v_axis || [0, 1, 0],
        width_mm: Math.max(0.001, Number(receiver.width_mm) || 1),
        height_mm: Math.max(0.001, Number(receiver.height_mm) || 1),
        resolution: [
          Math.max(1, parseInt(receiver.resolution_x, 10) || 80),
          Math.max(1, parseInt(receiver.resolution_y, 10) || 24)
        ],
        acceptance_angle_deg: Math.min(180, Math.max(0.1, Number(receiver.acceptance_angle_deg) || 90)),
        normal_flip: !!receiver.normal_flip,
        reference_mode: receiver.reference_mode || null,
        reference_vertex_indices: receiver.reference_vertex_indices || [],
        reference_edge_vertex_indices: receiver.reference_edge_vertex_indices || [],
        view_distance_mm: receiver.view_distance_mm ?? null,
        base_center: receiver.base_center || null,
        base_u_axis: receiver.base_u_axis || null,
        base_v_axis: receiver.base_v_axis || null,
        base_normal: receiver.base_normal || null,
        position_offset_mm: receiver.position_offset_mm || [0, 0, 0],
        tilt_xyz_deg: receiver.tilt_xyz_deg || [0, 0, 0],
        enabled: receiver.enabled !== false
      }};
    }}

    function syncReceiverFormPayload() {{
      receiverSpecsJson.value = JSON.stringify(state.receivers.map(receiverSpecPayload));
    }}

    function renderReceiverList() {{
      receiverList.innerHTML = '';
      receiverEmpty.style.display = state.receivers.length ? 'none' : 'block';
      for (const receiver of state.receivers) {{
        const row = document.createElement('div');
        row.className = 'emitter-list-row' + (receiver.receiver_id === state.activeReceiverId ? ' active' : '');
        row.setAttribute('data-receiver-id', receiver.receiver_id);
        const dot = document.createElement('span');
        dot.className = 'emitter-list-dot';
        const body = document.createElement('div');
        const name = document.createElement('div');
        name.className = 'emitter-list-name';
        name.textContent = receiver.display_name || receiver.receiver_id;
        const meta = document.createElement('div');
        meta.className = 'emitter-list-meta';
        meta.textContent = receiverPlacementLabel(receiver.placement_mode)
          + ' · ' + (Number(receiver.width_mm) || 0).toFixed(2)
          + ' × ' + (Number(receiver.height_mm) || 0).toFixed(2) + ' mm';
        body.appendChild(name);
        body.appendChild(meta);
        const acceptance = document.createElement('span');
        acceptance.className = 'emitter-list-power';
        acceptance.textContent = String(receiver.acceptance_angle_deg || 90) + '°';
        const actions = document.createElement('div');
        actions.className = 'tree-row-actions';
        const settingsButton = document.createElement('button');
        settingsButton.type = 'button';
        settingsButton.className = 'tree-action-btn';
        settingsButton.textContent = 'Settings';
        settingsButton.addEventListener('click', function (ev) {{
          editReceiver(receiver.receiver_id, ev);
        }});
        const deleteButton = document.createElement('button');
        deleteButton.type = 'button';
        deleteButton.className = 'tree-action-btn delete';
        deleteButton.textContent = 'Delete';
        deleteButton.addEventListener('click', function (ev) {{
          ev.stopPropagation();
          deleteReceiverById(receiver.receiver_id);
        }});
        actions.appendChild(settingsButton);
        actions.appendChild(deleteButton);
        row.appendChild(dot);
        row.appendChild(body);
        row.appendChild(acceptance);
        row.appendChild(actions);
        receiverList.appendChild(row);
      }}
      syncReceiverFormPayload();
      updateReceiverSelectionUI();
      updateRayTraceRunState();
    }}

    function updateReceiverGeometryUI() {{
      const placementMode = state.receiverDraftType || 'datum_plane';
      const isReference = placementMode === 'reference_plane';
      const isCurrentView = placementMode === 'current_view';
      receiverDatumSection.classList.toggle('hidden-block', placementMode !== 'datum_plane');
      receiverReferenceSection.classList.toggle('hidden-block', !isReference);
      receiverCurrentViewSection.classList.toggle('hidden-block', !isCurrentView);
      receiverAdjustmentSection.classList.toggle('hidden-block', !(isReference || isCurrentView));
      receiverReselectGeometryBtn.disabled = !isReference;
      receiverReselectGeometryBtn.textContent = isReference ? 'Select geometry' : (isCurrentView ? 'Camera geometry' : 'Numeric geometry');
      receiverWidthInput.disabled = isReference;
      receiverHeightInput.disabled = isReference;
      if (isReference) {{
        const isEdges = receiverReferenceModeSelect.value === 'two_edges';
        const selectedCount = isEdges ? state.receiverReferenceEdges.length : state.receiverReferenceVertices.length;
        receiverReferenceHint.textContent = isEdges
          ? '서로 다른 edge 2개를 선택합니다. 잘못 선택했으면 아래 Clear selected edges를 누르세요.'
          : 'vertex 3개 이상 선택 시 평면 preview가 생성됩니다. 최대 6개까지 선택하며 같은 점을 다시 누르면 제외됩니다.';
        receiverClearReferencesBtn.textContent = isEdges ? 'Clear selected edges' : 'Clear selected points';
        receiverClearReferencesBtn.disabled = selectedCount === 0;
        receiverReferenceCount.textContent = isEdges ? (selectedCount + ' / 2 selected') : (selectedCount + ' / 6 selected · minimum 3');
      }}
    }}

    function updateReceiverDraftSummary() {{
      const placementMode = state.receiverDraftType || 'datum_plane';
      const plane = currentDraftReceiverPlane();
      let summary = 'Receiver geometry not set';
      let area = 0;
      let normal = [0, 0, 1];
      if (plane && plane.center) {{
        area = Math.max(0, plane.widthMm * plane.heightMm);
        normal = plane.normal || normal;
        if (placementMode === 'reference_plane') {{
          receiverWidthInput.value = plane.widthMm.toFixed(6);
          receiverHeightInput.value = plane.heightMm.toFixed(6);
        }}
        summary = 'Type: ' + receiverPlacementLabel(placementMode)
          + '\\nCenter: (' + plane.center.map((value) => Number(value).toFixed(3)).join(', ') + ') mm'
          + '\\nSize: ' + plane.widthMm.toFixed(3) + ' × ' + plane.heightMm.toFixed(3) + ' mm';
        if (placementMode === 'reference_plane' && Number(plane.planarityErrorMm) > 0.001) {{
          summary += '\\nPlanarity deviation: ' + plane.planarityErrorMm.toFixed(4) + ' mm';
        }}
      }} else if (placementMode === 'reference_plane') {{
        const isEdges = receiverReferenceModeSelect.value === 'two_edges';
        const selectedCount = isEdges ? state.receiverReferenceEdges.length : state.receiverReferenceVertices.length;
        summary = 'Type: Reference geometry\\n' + (isEdges ? 'Edges: ' + selectedCount + ' / 2' : 'Vertices: ' + selectedCount + ' / 3–6');
      }}
      const shownNormal = receiverNormalFlipInput.checked ? normal.map((value) => -value) : normal;
      receiverAreaInput.value = area.toFixed(3);
      receiverGeometrySummary.textContent = summary
        + '\\nArea: ' + area.toFixed(3) + ' mm²'
        + '\\nReceiving normal: (' + shownNormal.map((value) => Number(value).toFixed(4)).join(', ') + ')';
      cursorReceiverChip.textContent = currentReceiver()
        ? 'Edit ' + receiverPlacementLabel(placementMode)
        : 'New ' + receiverPlacementLabel(placementMode);
      if (placementMode === 'datum_plane') {{
        cursorReceiverNameHint.textContent = '좌표·크기·회전각으로 빈 공간의 가상 측정면을 정의합니다.';
      }} else if (placementMode === 'current_view') {{
        cursorReceiverNameHint.textContent = '현재 카메라가 바라보는 방향을 수광 normal로 저장합니다.';
      }} else {{
        cursorReceiverNameHint.textContent = plane && plane.center
          ? '선택한 CAD reference 사이에 가상 측정면이 생성되었습니다.'
          : '3D viewer에서 기준 geometry를 선택하세요.';
      }}
      updateReceiverGeometryUI();
    }}

    function updateReceiverSelectionUI() {{
      const placementMode = state.receiverDraftType || 'datum_plane';
      const isReferenceSelecting = state.receiverSelectionActive && placementMode === 'reference_plane';
      addDatumReceiverBtn.textContent = '+ Datum plane receiver';
      addReferenceReceiverBtn.textContent = isReferenceSelecting ? 'Selecting references…' : '+ Reference geometry receiver';
      addCurrentViewReceiverBtn.textContent = '+ Current view receiver';
      addDatumReceiverBtn.disabled = state.receiverSelectionActive;
      addReferenceReceiverBtn.disabled = state.receiverSelectionActive;
      addCurrentViewReceiverBtn.disabled = state.receiverSelectionActive;
      receiverSelectionBanner.classList.toggle('active', state.receiverSelectionActive);
      if (isReferenceSelecting) {{
        const isEdges = receiverReferenceModeSelect.value === 'two_edges';
        const selectedCount = isEdges ? state.receiverReferenceEdges.length : state.receiverReferenceVertices.length;
        receiverSelectionBanner.textContent = isEdges
          ? ('Reference edge 선택: ' + selectedCount + ' / 2 · Apply로 확정')
          : ('Reference vertex 선택: ' + selectedCount + ' / 3–6 · 최소 3개 선택 후 Apply');
        viewerTip.textContent = isEdges ? 'Receiver: Click near two CAD edges.' : 'Receiver: Select 3 to 6 CAD vertices. Click again to remove.';
      }} else if (state.receivers.length) {{
        receiverSelectionBanner.textContent = state.receivers.length + ' receiver(s) registered. List의 Settings에서 편집합니다.';
        if (!state.emitterSelectionActive) viewerTip.textContent = 'Drag = rotate, Middle drag = rotate, Wheel = zoom, Right click = component menu, Right drag = pan, Shift/Alt+drag = roll.';
      }} else {{
        receiverSelectionBanner.textContent = 'Receiver가 없습니다. Add에서 배치 방식을 선택하세요.';
        if (!state.emitterSelectionActive) viewerTip.textContent = 'Drag = rotate, Middle drag = rotate, Wheel = zoom, Right click = component menu, Right drag = pan, Shift/Alt+drag = roll.';
      }}
    }}

    function resetReceiverEditorValues() {{
      const receiver = currentReceiver();
      const center = receiver && receiver.center ? receiver.center : modelCenterMm();
      const rotation = receiver && receiver.rotation_deg ? receiver.rotation_deg : {{ x: 0, y: 0, z: 0 }};
      const positionOffset = receiver && Array.isArray(receiver.position_offset_mm) ? receiver.position_offset_mm : [0, 0, 0];
      const tiltAdjustment = receiver && Array.isArray(receiver.tilt_xyz_deg) ? receiver.tilt_xyz_deg : [0, 0, 0];
      receiverNameInput.value = receiver ? receiver.display_name : ('Receiver ' + state.receiverSequence);
      receiverWidthInput.value = receiver ? String(receiver.width_mm ?? 100) : '100';
      receiverHeightInput.value = receiver ? String(receiver.height_mm ?? 30) : '30';
      receiverCenterX.value = String(center[0] ?? 0);
      receiverCenterY.value = String(center[1] ?? 0);
      receiverCenterZ.value = String(center[2] ?? 0);
      receiverRotationX.value = String(rotation.x ?? 0);
      receiverRotationY.value = String(rotation.y ?? 0);
      receiverRotationZ.value = String(rotation.z ?? 0);
      receiverOffsetX.value = String(positionOffset[0] ?? 0);
      receiverOffsetY.value = String(positionOffset[1] ?? 0);
      receiverOffsetZ.value = String(positionOffset[2] ?? 0);
      receiverTiltX.value = String(tiltAdjustment[0] ?? 0);
      receiverTiltY.value = String(tiltAdjustment[1] ?? 0);
      receiverTiltZ.value = String(tiltAdjustment[2] ?? 0);
      receiverReferenceModeSelect.value = receiver ? (receiver.reference_mode || 'three_vertices') : 'three_vertices';
      receiverViewDistanceInput.value = receiver ? String(receiver.view_distance_mm ?? Math.max(50, modelSpanMm() * 0.25)) : String(Math.max(50, modelSpanMm() * 0.25));
      receiverResolutionX.value = receiver ? String(receiver.resolution_x ?? 80) : '80';
      receiverResolutionY.value = receiver ? String(receiver.resolution_y ?? 24) : '24';
      receiverAcceptanceInput.value = receiver ? String(receiver.acceptance_angle_deg ?? 90) : '90';
      receiverNormalFlipInput.checked = receiver ? !!receiver.normal_flip : false;
      receiverDeleteBtn.disabled = false;
      receiverDeleteBtn.textContent = receiver ? 'Delete receiver' : 'Discard draft';
      if (receiver && receiver.placement_mode === 'current_view') {{
        state.receiverCurrentViewPlane = receiverBasePlaneFromSpec(receiver);
      }}
      updateReceiverGeometryUI();
      updateReceiverDraftSummary();
    }}

    function clampReceiverPopupPosition(left, top) {{
      const rect = viewerWrap.getBoundingClientRect();
      const popupWidth = cursorReceiverPopup.offsetWidth || 382;
      const popupHeight = cursorReceiverPopup.offsetHeight || 620;
      return {{
        left: Math.min(Math.max(14, rect.width - popupWidth - 14), Math.max(14, left)),
        top: Math.min(Math.max(24, rect.height - popupHeight - 14), Math.max(24, top))
      }};
    }}

    function applyReceiverPopupPosition(left, top) {{
      const next = clampReceiverPopupPosition(left, top);
      cursorReceiverPopup.style.left = next.left + 'px';
      cursorReceiverPopup.style.top = next.top + 'px';
      state.receiverPopupPosition = next;
    }}

    function showReceiverPopupAt(clientX, clientY) {{
      cursorReceiverPopup.classList.remove('hidden-block');
      if (clientX !== null && clientX !== undefined && clientY !== null && clientY !== undefined) {{
        const rect = viewerWrap.getBoundingClientRect();
        applyReceiverPopupPosition(clientX - rect.left + 14, clientY - rect.top + 14);
      }} else if (state.receiverPopupPosition) {{
        applyReceiverPopupPosition(state.receiverPopupPosition.left, state.receiverPopupPosition.top);
      }} else {{
        const rect = viewerWrap.getBoundingClientRect();
        applyReceiverPopupPosition(rect.width - (cursorReceiverPopup.offsetWidth || 382) - 18, 42);
      }}
    }}

    function hideReceiverPopup() {{
      cursorReceiverPopup.classList.add('hidden-block');
    }}

    function startReceiverPopupDrag(ev) {{
      if (ev.target && ev.target.closest('button')) return;
      const popupRect = cursorReceiverPopup.getBoundingClientRect();
      state.receiverPopupDrag.active = true;
      state.receiverPopupDrag.offsetX = ev.clientX - popupRect.left;
      state.receiverPopupDrag.offsetY = ev.clientY - popupRect.top;
      cursorReceiverPopup.classList.add('is-dragging');
      ev.preventDefault();
    }}

    function moveReceiverPopupDrag(ev) {{
      if (!state.receiverPopupDrag.active) return;
      const rect = viewerWrap.getBoundingClientRect();
      applyReceiverPopupPosition(
        ev.clientX - rect.left - state.receiverPopupDrag.offsetX,
        ev.clientY - rect.top - state.receiverPopupDrag.offsetY
      );
    }}

    function stopReceiverPopupDrag() {{
      if (!state.receiverPopupDrag.active) return;
      state.receiverPopupDrag.active = false;
      cursorReceiverPopup.classList.remove('is-dragging');
    }}

    function beginReceiverCreation(placementMode) {{
      state.emitterSelectionActive = false;
      hideEmitterPopup();
      updateEmitterSelectionUI();
      state.activeReceiverId = null;
      state.receiverDraftType = placementMode;
      state.receiverReferenceVertices = [];
      state.receiverReferenceEdges = [];
      state.receiverCurrentViewPlane = null;
      state.receiverSelectionActive = placementMode === 'reference_plane';
      resetReceiverEditorValues();
      switchSideTab('raytracing', {{ forceOpen: true }});
      showReceiverPopupAt(null, null);
      renderReceiverList();
      drawViewer();
      if (placementMode === 'current_view') {{
        captureCurrentViewReceiverPlane();
        updateReceiverDraftSummary();
        drawViewer();
      }}
    }}

    function editReceiver(receiverId, popupPosition) {{
      const receiver = receiverById(receiverId);
      if (!receiver) return;
      state.activeReceiverId = receiverId;
      state.receiverDraftType = receiver.placement_mode || 'datum_plane';
      state.receiverReferenceVertices = Array.from(receiver.reference_vertex_indices || []).slice(0, 6);
      state.receiverReferenceEdges = (receiver.reference_edge_vertex_indices || []).map((edge) => Array.from(edge));
      state.receiverSelectionActive = false;
      state.receiverCurrentViewPlane = state.receiverDraftType === 'current_view' ? receiverBasePlaneFromSpec(receiver) : null;
      resetReceiverEditorValues();
      showReceiverPopupAt(
        popupPosition && popupPosition.clientX !== undefined ? popupPosition.clientX : null,
        popupPosition && popupPosition.clientY !== undefined ? popupPosition.clientY : null
      );
      renderReceiverList();
      drawViewer();
    }}

    function reselectReceiverGeometry() {{
      if (state.receiverDraftType !== 'reference_plane') return;
      state.receiverSelectionActive = true;
      updateReceiverSelectionUI();
      drawViewer();
    }}

    function cancelReceiverSelection() {{
      state.receiverSelectionActive = false;
      if (!currentReceiver()) {{
        state.receiverReferenceVertices = [];
        state.receiverReferenceEdges = [];
        hideReceiverPopup();
      }}
      updateReceiverSelectionUI();
      drawViewer();
    }}

    function clearReceiverReferenceSelection() {{
      state.receiverReferenceVertices = [];
      state.receiverReferenceEdges = [];
      state.receiverSelectionActive = state.receiverDraftType === 'reference_plane';
      updateReceiverDraftSummary();
      updateReceiverSelectionUI();
      drawViewer();
    }}

    function handleReceiverGeometryPick(faceIndex, pickEvent) {{
      if (faceIndex === null || faceIndex === undefined || state.receiverDraftType !== 'reference_plane') return;
      let maximumReached = false;
      if ((receiverReferenceModeSelect.value || 'three_vertices') === 'two_edges') {{
        const edge = pickEvent && Array.isArray(pickEvent.edgeVertexIndices) ? pickEvent.edgeVertexIndices.map(Number).sort((a, b) => a - b) : [];
        if (edge.length !== 2 || edge.some((value) => !Number.isInteger(value))) return;
        const signature = edge.join(':');
        const existing = state.receiverReferenceEdges.findIndex((item) => item.slice().sort((a, b) => a - b).join(':') === signature);
        if (existing >= 0) state.receiverReferenceEdges.splice(existing, 1);
        else if (state.receiverReferenceEdges.length < 2) state.receiverReferenceEdges.push(edge);
        else state.receiverReferenceEdges = [state.receiverReferenceEdges[1], edge];
      }} else {{
        const vertexIndex = Number(pickEvent && pickEvent.vertexIndex);
        if (!Number.isInteger(vertexIndex) || vertexIndex < 0) return;
        const existing = state.receiverReferenceVertices.indexOf(vertexIndex);
        if (existing >= 0) state.receiverReferenceVertices.splice(existing, 1);
        else if (state.receiverReferenceVertices.length < 6) state.receiverReferenceVertices.push(vertexIndex);
        else maximumReached = true;
      }}
      updateReceiverDraftSummary();
      updateReceiverSelectionUI();
      if (maximumReached) {{
        receiverSelectionBanner.textContent = 'vertex는 최대 6개까지 선택할 수 있습니다. 다른 점으로 바꾸려면 기존 점을 다시 클릭하거나 Clear selected points를 누르세요.';
      }}
      if (cursorReceiverPopup.classList.contains('hidden-block')) showReceiverPopupAt(null, null);
      drawViewer();
    }}

    function applyReceiverEditor() {{
      const placementMode = state.receiverDraftType || 'datum_plane';
      if (placementMode === 'current_view' && !state.receiverCurrentViewPlane) captureCurrentViewReceiverPlane();
      const basePlane = currentBaseDraftReceiverPlane();
      const plane = placementMode === 'datum_plane' ? basePlane : applyReceiverAdjustment(basePlane);
      if (!plane || !plane.center) {{
        receiverSelectionBanner.textContent = receiverReferenceModeSelect.value === 'two_edges'
          ? 'Receiver를 만들려면 서로 다른 edge 2개를 선택하세요.'
          : 'Receiver를 만들려면 서로 일직선이 아닌 vertex를 3~6개 선택하세요.';
        receiverSelectionBanner.classList.add('active');
        return;
      }}
      let receiver = currentReceiver();
      if (!receiver) {{
        receiver = {{ receiver_id: 'receiver_' + String(state.receiverSequence).padStart(3, '0') }};
        state.receiverSequence += 1;
        state.receivers.push(receiver);
      }}
      receiver.display_name = receiverNameInput.value.trim() || receiver.receiver_id;
      receiver.receiver_type = 'rectangle';
      receiver.placement_mode = placementMode;
      receiver.center = Array.from(plane.center);
      receiver.u_axis = Array.from(plane.uAxis || [1, 0, 0]);
      receiver.v_axis = Array.from(plane.vAxis || [0, 1, 0]);
      receiver.normal = Array.from(plane.normal || [0, 0, 1]);
      receiver.width_mm = Math.max(0.001, Number(plane.widthMm) || 1);
      receiver.height_mm = Math.max(0.001, Number(plane.heightMm) || 1);
      receiver.resolution_x = Math.max(1, parseInt(receiverResolutionX.value, 10) || 80);
      receiver.resolution_y = Math.max(1, parseInt(receiverResolutionY.value, 10) || 24);
      receiver.acceptance_angle_deg = Math.min(180, Math.max(0.1, parseFloat(receiverAcceptanceInput.value) || 90));
      receiver.normal_flip = !!receiverNormalFlipInput.checked;
      receiver.reference_mode = placementMode === 'reference_plane' ? (receiverReferenceModeSelect.value || 'three_vertices') : null;
      receiver.reference_vertex_indices = placementMode === 'reference_plane' ? Array.from(state.receiverReferenceVertices) : [];
      receiver.reference_edge_vertex_indices = placementMode === 'reference_plane' ? state.receiverReferenceEdges.map((edge) => Array.from(edge)) : [];
      receiver.view_distance_mm = placementMode === 'current_view' ? Math.max(0.001, parseMoveFieldValue(receiverViewDistanceInput.value)) : null;
      const adjustment = receiverAdjustmentFromInputs();
      receiver.base_center = placementMode === 'datum_plane' || !basePlane ? null : Array.from(basePlane.center || [0, 0, 0]);
      receiver.base_u_axis = placementMode === 'datum_plane' || !basePlane ? null : Array.from(basePlane.uAxis || [1, 0, 0]);
      receiver.base_v_axis = placementMode === 'datum_plane' || !basePlane ? null : Array.from(basePlane.vAxis || [0, 1, 0]);
      receiver.base_normal = placementMode === 'datum_plane' || !basePlane ? null : Array.from(basePlane.normal || [0, 0, 1]);
      receiver.position_offset_mm = placementMode === 'datum_plane' ? [0, 0, 0] : Array.from(adjustment.offset);
      receiver.tilt_xyz_deg = placementMode === 'datum_plane'
        ? [0, 0, 0]
        : [adjustment.tilt.x, adjustment.tilt.y, adjustment.tilt.z];
      receiver.rotation_deg = placementMode === 'datum_plane' ? {{
        x: parseMoveFieldValue(receiverRotationX.value),
        y: parseMoveFieldValue(receiverRotationY.value),
        z: parseMoveFieldValue(receiverRotationZ.value)
      }} : {{ x: 0, y: 0, z: 0 }};
      receiver.enabled = true;
      state.activeReceiverId = receiver.receiver_id;
      state.receiverSelectionActive = false;
      invalidateDirectRayTraceResult();
      resetReceiverEditorValues();
      renderReceiverList();
      drawViewer();
    }}

    function deleteCurrentReceiver() {{
      const deletedSavedReceiver = !!state.activeReceiverId;
      if (deletedSavedReceiver) {{
        state.receivers = state.receivers.filter((item) => item.receiver_id !== state.activeReceiverId);
      }}
      state.activeReceiverId = null;
      state.receiverDraftType = 'datum_plane';
      state.receiverReferenceVertices = [];
      state.receiverReferenceEdges = [];
      state.receiverCurrentViewPlane = null;
      state.receiverSelectionActive = false;
      invalidateDirectRayTraceResult();
      hideReceiverPopup();
      renderReceiverList();
      drawViewer();
      receiverSelectionBanner.textContent = deletedSavedReceiver ? 'Receiver를 삭제했습니다.' : '생성 중인 receiver preview를 삭제했습니다.';
      receiverSelectionBanner.classList.add('active');
    }}

    function deleteReceiverById(receiverId) {{
      if (!receiverById(receiverId)) return;
      state.activeReceiverId = receiverId;
      deleteCurrentReceiver();
    }}

    function resetReceiversForScene() {{
      state.receivers = [];
      state.activeReceiverId = null;
      state.receiverDraftType = 'datum_plane';
      state.receiverReferenceVertices = [];
      state.receiverReferenceEdges = [];
      state.receiverCurrentViewPlane = null;
      state.receiverSelectionActive = false;
      state.receiverSequence = 1;
      hideReceiverPopup();
      renderReceiverList();
    }}

    function resetGapSelection() {{
      state.selectedGapObjectId = null;
      state.selectedGapObjectIds = new Set();
      state.selectedMaterialObjectId = null;
      state.transformRules = [];
      state.activeTransformRuleId = null;
      state.selectedTransformRuleIds = new Set();
      state.selectedGapFaces = new Set();
      state.localGapFaces = new Set();
      state.gapMove = {{ x: 0, y: 0, z: 0 }};
      state.gapMoveText = {{ x: '0', y: '0', z: '0' }};
      state.gapTilt = {{ x: 0, y: 0, z: 0 }};
      state.gapTiltText = {{ x: '0', y: '0', z: '0' }};
      if (gapFaceInput) {{
        gapFaceInput.value = '';
      }}
      hideMovePopup();
      hideMaterialPopup();
      syncTransformInputs();
      syncComponentSelectionSummary();
      updateMaterialTargetSummary();
      renderTransformRules();
      updateGapSelectionStats();
    }}

    async function uploadCadFile(file) {{
      const fileName = (file && file.name) ? file.name : 'unknown';
      runBtn.disabled = true;
      importCadBtn.disabled = true;
      useSampleBtn.disabled = true;
      setResultMessage('<div>Uploading CAD file...</div>');
      try {{
        const res = await fetch('/api/upload?filename=' + encodeURIComponent(fileName), {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/octet-stream' }},
          body: file
        }});
        if (!res.ok) {{
          const txt = await res.text();
          throw new Error(txt || 'Upload failed');
        }}
        const payload = await res.json();
        cadInput.value = payload.path || '';
        cadFileName.value = payload.display_name || fileName;
        cadMeta.textContent = 'Uploaded: ' + (payload.display_name || fileName) + ' / stored for this session';
        await loadScene();
      }} catch (err) {{
        setResultMessage('<div><b>Upload failed:</b> ' + err.message + '</div>');
      }} finally {{
        importCadBtn.disabled = false;
        useSampleBtn.disabled = false;
        updateRayTraceRunState();
      }}
    }}

    async function loadScene() {{
      runBtn.disabled = true;
      importCadBtn.disabled = true;
      useSampleBtn.disabled = true;
      runBtn.textContent = 'Loading CAD...';
      const cad = cadInput.value.trim();
      const endpoint = '/api/scene?cad=' + encodeURIComponent(cad);
      try {{
        const res = await fetch(endpoint);
        if (!res.ok) {{
          const errorText = await res.text();
          throw new Error(errorText || ('Scene API error (' + res.status + ')'));
        }}
        const payload = await res.json();
        if (!payload || !payload.mesh || !Array.isArray(payload.mesh.vertices) || !Array.isArray(payload.mesh.faces)) {{
          throw new Error('CAD scene response does not contain a valid triangle mesh');
        }}
        if (!payload.mesh.vertices.length || !payload.mesh.faces.length) {{
          throw new Error('CAD tessellation produced an empty triangle mesh');
        }}
        state.mesh = payload.mesh;
        state.sceneToken = payload.metadata?.scene_token || null;
        state.rayTraceResult = null;
        state.hiddenComponentObjectIds.clear();
        state.nonTraceableComponentObjectIds.clear();
        state.deletedComponentObjectIds.clear();
        hideComponentContextMenu();
        pendingThreeCameraPreset = 'fit';
        drawViewer();
        state.objectsById.clear();
        state.faceToObjectId.clear();
        buildFaceAdjacency();
        objectList.innerHTML = '';
        gapObjectList.innerHTML = '';
        resetRoiSelection();
        resetGapSelection();
        resetEmittersForScene();
        resetReceiversForScene();

        if (!payload.objects.length) {{
          objectList.innerHTML = '<div class=\"small\">No object split detected. You can input faces manually.</div>';
          gapObjectList.innerHTML = '<div class=\"small\">No component split detected yet.</div>';
        }} else {{
          for (const item of payload.objects) {{
            state.objectsById.set(item.object_id, item);
            for (const faceIndex of item.face_indices) {{
              state.faceToObjectId.set(faceIndex, item.object_id);
            }}
            const row = document.createElement('div');
            row.className = 'object-item';
            row.setAttribute('data-roi-object-row-id', String(item.object_id));
            row.innerHTML = '<label><input type=\"checkbox\" data-id=\"' + item.object_id + '\"/> <span class=\"roi-object-label\"></span></label>';
            row.querySelector('.roi-object-label').textContent = objectLabel(item);
            const cb = row.querySelector('input');
            cb.addEventListener('change', function (ev) {{
              const id = parseInt(ev.target.getAttribute('data-id'), 10);
              if (ev.target.checked) {{
                state.selectedObjectIds.add(id);
              }} else {{
                state.selectedObjectIds.delete(id);
              }}
              refreshSelectionFromObject();
            }});
            objectList.appendChild(row);

            const gapRow = document.createElement('div');
            gapRow.className = 'object-item';
            gapRow.setAttribute('data-component-row-id', String(item.object_id));
            gapRow.innerHTML =
              '<div class=\"component-tree-row\">'
              + '<div class=\"component-row-main\" data-component-select=\"' + item.object_id + '\">'
              + '<div class=\"name\" data-component-name=\"' + item.object_id + '\" tabindex=\"0\" title=\"Double-click or press F2 to rename\"></div>'
              + '<div class=\"meta\">faces: ' + item.face_count + ' / area: ' + item.area_mm2 + ' mm2</div>'
              + '</div>'
              + '<div class=\"component-row-actions\">'
              + '<button type=\"button\" class=\"mini-btn component-material\" data-component-material=\"' + item.object_id + '\">Material</button>'
              + '<button type=\"button\" class=\"mini-btn component-transform\" data-component-transform=\"' + item.object_id + '\">Transform</button>'
              + '<button type=\"button\" class=\"mini-btn traceability-toggle\" data-component-traceability=\"' + item.object_id + '\" aria-pressed=\"true\">Trace Off</button>'
              + '<button type=\"button\" class=\"mini-btn component-more\" data-component-more=\"' + item.object_id + '\" aria-haspopup=\"menu\">+</button>'
              + '</div>'
              + '</div>';
            const selectArea = gapRow.querySelector('[data-component-select]');
            const nameEl = gapRow.querySelector('[data-component-name]');
            const materialBtn = gapRow.querySelector('[data-component-material]');
            const transformBtn = gapRow.querySelector('[data-component-transform]');
            const traceabilityBtn = gapRow.querySelector('[data-component-traceability]');
            const moreBtn = gapRow.querySelector('[data-component-more]');
            nameEl.textContent = item.object_name;
            nameEl.title = item.object_name + ' · Double-click or press F2 to rename';
            materialBtn.setAttribute('aria-label', 'Material for ' + item.object_name);
            transformBtn.setAttribute('aria-label', 'Transform ' + item.object_name);
            moreBtn.setAttribute('aria-label', 'Hide, show, or delete ' + item.object_name);
            moreBtn.title = 'Hide / Show / Delete';
            selectArea.addEventListener('click', function (ev) {{
              const id = parseInt(ev.currentTarget.getAttribute('data-component-select'), 10);
              setSelectedGapObject(id, null, true);
            }});
            selectArea.addEventListener('dblclick', function (ev) {{
              const nameTarget = ev.target.closest('[data-component-name]');
              if (!nameTarget) return;
              ev.preventDefault();
              ev.stopPropagation();
              const id = parseInt(nameTarget.getAttribute('data-component-name'), 10);
              beginRenameComponent(id);
            }});
            selectArea.addEventListener('keydown', function (ev) {{
              if (ev.key !== 'F2') return;
              ev.preventDefault();
              ev.stopPropagation();
              const id = parseInt(ev.currentTarget.getAttribute('data-component-select'), 10);
              beginRenameComponent(id);
            }});
            materialBtn.addEventListener('click', function (ev) {{
              ev.stopPropagation();
              const id = parseInt(ev.currentTarget.getAttribute('data-component-material'), 10);
              focusMaterialForObject(id, ev);
            }});
            transformBtn.addEventListener('click', function (ev) {{
              ev.stopPropagation();
              const id = parseInt(ev.currentTarget.getAttribute('data-component-transform'), 10);
              startTransformForObject(id, ev);
            }});
            traceabilityBtn.addEventListener('click', function (ev) {{
              ev.stopPropagation();
              const id = parseInt(ev.currentTarget.getAttribute('data-component-traceability'), 10);
              toggleComponentTraceability(id);
            }});
            moreBtn.addEventListener('click', function (ev) {{
              ev.preventDefault();
              ev.stopPropagation();
              const id = parseInt(ev.currentTarget.getAttribute('data-component-more'), 10);
              const rect = ev.currentTarget.getBoundingClientRect();
              showComponentContextMenu(id, rect.left, rect.bottom + 4, 'compact');
            }});
            gapRow.addEventListener('contextmenu', function (ev) {{
              ev.preventDefault();
              ev.stopPropagation();
              showComponentContextMenu(item.object_id, ev.clientX, ev.clientY);
            }});
            gapObjectList.appendChild(gapRow);
            syncComponentVisibilityDom(item.object_id);
            syncComponentTraceabilityDom(item.object_id);
          }}
        }}

        kpiFaces.textContent = String(payload.metadata.face_count);
        kpiVerts.textContent = String(payload.metadata.vertex_count);
        kpiMode.textContent = payload.metadata.synthetic ? 'Synthetic' : 'CAD';
        if (!cad) {{
          cadFileName.value = 'Sample geometry (no CAD file)';
        }}
        cadMeta.textContent = 'Loaded: ' + (payload.metadata.source_file || 'sample geometry')
          + ' / ' + payload.metadata.face_count + ' faces'
          + ' / ' + payload.metadata.component_count + ' components'
          + ' / ' + payload.metadata.import_note;
        syncComponentSelectionSummary();
        renderMaterialLibrary();
        updateMaterialTargetSummary();
        renderTransformRules();
        updateViewerMode();
        updateGapSelectionStats();
        pendingThreeCameraPreset = 'fit';
        drawViewer();
        const loadedMesh = state.mesh;
        window.requestAnimationFrame(function () {{
          if (state.mesh !== loadedMesh) return;
          pendingThreeCameraPreset = 'fit';
          drawViewer();
        }});
      }} catch (err) {{
        state.sceneToken = null;
        cadMeta.textContent = 'Load failed: ' + err.message;
        setResultMessage('<div><b>Load failed:</b> ' + err.message + '</div>');
      }} finally {{
        importCadBtn.disabled = false;
        useSampleBtn.disabled = false;
        updateRayTraceRunState();
      }}
    }}

    function drawViewerOn(canvas, mode) {{
      const ctx = canvas.getContext('2d');
      if (!state.mesh) {{
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        return;
      }}

      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      canvas.width = w * window.devicePixelRatio;
      canvas.height = h * window.devicePixelRatio;
      ctx.setTransform(window.devicePixelRatio, 0, 0, window.devicePixelRatio, 0, 0);
      const scene = buildProjectedScene(canvas);
      if (!scene) return;
      if (mode === 'full') {{
        state.renderScenes.full = scene;
        updateCoordReadout(scene);
      }} else {{
        state.renderScenes.roi = scene;
      }}

      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = '#020617';
      ctx.fillRect(0, 0, w, h);
      const renderModeValue = state.renderMode || 'wireframe';
      const committedFaces = mode === 'full' ? getCommittedTransformFaceSet() : new Set();
      const hiddenComponentFaces = getViewerExcludedFaceSet();
      for (const tri of scene.triList) {{
        if (hiddenComponentFaces.has(tri.idx)) continue;
        const sel = state.selectedFaces.has(tri.idx);
        const inspected = state.inspectedFaceIndex === tri.idx;
        const gapSelected = state.gapTargetMode === 'component_move_gap'
          ? state.selectedGapObjectIds.has(state.faceToObjectId.get(tri.idx))
          : state.selectedGapFaces.has(tri.idx);
        const materialSelected = state.selectedMaterialObjectId !== null
          && state.selectedMaterialObjectId === state.faceToObjectId.get(tri.idx);
        if (mode === 'roi' && !sel) {{
          continue;
        }}
        if (mode === 'full' && committedFaces.has(tri.idx)) {{
          continue;
        }}
        ctx.beginPath();
        ctx.moveTo(tri.p0.screenX, tri.p0.screenY);
        ctx.lineTo(tri.p1.screenX, tri.p1.screenY);
        ctx.lineTo(tri.p2.screenX, tri.p2.screenY);
        ctx.closePath();
        if (mode === 'roi') {{
          ctx.fillStyle = sel ? 'rgba(56, 189, 248, 0.82)' : 'rgba(14, 165, 233, 0.0)';
          ctx.strokeStyle = '#f8fafc';
          ctx.lineWidth = 1.2;
          ctx.fill();
          ctx.stroke();
          continue;
        }}

        if (renderModeValue === 'wireframe') {{
          ctx.fillStyle = sel ? 'rgba(56, 189, 248, 0.18)' : (inspected ? 'rgba(250, 204, 21, 0.16)' : (gapSelected ? 'rgba(244, 114, 182, 0.08)' : (materialSelected ? 'rgba(45, 212, 191, 0.10)' : 'rgba(14, 165, 233, 0.06)')));
          ctx.strokeStyle = sel ? '#93c5fd' : (inspected ? '#facc15' : (gapSelected ? 'rgba(244, 114, 182, 0.82)' : (materialSelected ? 'rgba(45, 212, 191, 0.90)' : 'rgba(148, 163, 184, 0.72)')));
          ctx.lineWidth = sel ? 1.1 : (inspected ? 1.0 : (gapSelected ? 0.9 : (materialSelected ? 0.95 : 0.55)));
          ctx.fill();
          ctx.stroke();
        }} else if (renderModeValue === 'surface') {{
          const shade = Math.round(52 + tri.shade * 138);
          const blue = Math.min(255, shade + 36);
          ctx.fillStyle = sel ? 'rgba(56, 189, 248, 0.82)' : (inspected ? 'rgba(250, 204, 21, 0.46)' : (gapSelected ? 'rgba(244, 114, 182, 0.20)' : (materialSelected ? 'rgba(45, 212, 191, 0.22)' : 'rgba(' + shade + ',' + (shade + 20) + ',' + blue + ',0.72)')));
          ctx.strokeStyle = sel ? 'rgba(224, 242, 254, 0.92)' : (inspected ? 'rgba(250, 204, 21, 0.95)' : (gapSelected ? 'rgba(244, 114, 182, 0.80)' : (materialSelected ? 'rgba(45, 212, 191, 0.90)' : 'rgba(30, 41, 59, 0.42)')));
          ctx.lineWidth = sel ? 1.0 : (inspected ? 1.0 : (gapSelected ? 0.6 : (materialSelected ? 0.75 : 0.35)));
          ctx.fill();
          ctx.stroke();
        }} else {{
          const shade = Math.round(48 + tri.shade * 145);
          const blue = Math.min(255, shade + 34);
          ctx.fillStyle = sel ? 'rgba(56, 189, 248, 0.90)' : (inspected ? 'rgba(250, 204, 21, 0.62)' : (gapSelected ? 'rgba(244, 114, 182, 0.26)' : (materialSelected ? 'rgba(45, 212, 191, 0.30)' : 'rgb(' + shade + ',' + (shade + 18) + ',' + blue + ')')));
          ctx.strokeStyle = sel ? '#e0f2fe' : (inspected ? '#fde047' : (gapSelected ? 'rgba(244, 114, 182, 0.90)' : (materialSelected ? 'rgba(45, 212, 191, 0.95)' : 'rgba(15, 23, 42, 0.82)')));
          ctx.lineWidth = sel ? 1.15 : (inspected ? 1.2 : (gapSelected ? 0.95 : (materialSelected ? 1.05 : 0.85)));
          ctx.fill();
          ctx.stroke();
        }}
      }}

      if (mode === 'full') {{
        drawCommittedTransforms(ctx, scene);
        drawDraftTransformPreview(ctx, scene);
      }}
      if (state.selectionBox.active && state.selectionBox.canvasMode === mode) {{
        const x = Math.min(state.selectionBox.startX, state.selectionBox.currentX);
        const y = Math.min(state.selectionBox.startY, state.selectionBox.currentY);
        const boxW = Math.abs(state.selectionBox.currentX - state.selectionBox.startX);
        const boxH = Math.abs(state.selectionBox.currentY - state.selectionBox.startY);
        ctx.save();
        ctx.fillStyle = 'rgba(250, 204, 21, 0.12)';
        ctx.strokeStyle = 'rgba(250, 204, 21, 0.95)';
        ctx.lineWidth = 1.0;
        ctx.fillRect(x, y, boxW, boxH);
        ctx.strokeRect(x, y, boxW, boxH);
        ctx.restore();
      }}
      drawAxisOverlay(ctx, scene, w, h);

      if (mode === 'roi' && state.selectedFaces.size === 0) {{
        ctx.fillStyle = '#94a3b8';
        ctx.font = '14px Segoe UI';
        ctx.textAlign = 'center';
        ctx.fillText('Select ROI on the left, then this becomes the main view', w / 2, h / 2);
        ctx.textAlign = 'start';
      }}
    }}

    function toggleClickedFace(faceIndex) {{
      if (faceIndex === null || faceIndex === undefined) return;
      if (state.clickedFaces.has(faceIndex)) {{
        state.clickedFaces.delete(faceIndex);
      }} else {{
        state.clickedFaces.add(faceIndex);
      }}
      recomputeSelectedFaces();
    }}

    function setInspectedFace(faceIndex) {{
      state.inspectedFaceIndex = (faceIndex === null || faceIndex === undefined) ? null : faceIndex;
      drawViewer();
    }}

    function parseMoveFieldValue(raw) {{
      const normalized = String(raw ?? '').trim();
      if (!normalized || normalized === '-' || normalized === '+' || normalized === '.' || normalized === '-.' || normalized === '+.') {{
        return 0;
      }}
      const value = parseFloat(normalized);
      return Number.isFinite(value) ? value : 0;
    }}

    function applyCameraPreset(preset) {{
      const defaultDistance = 1.8;
      if (preset === 'fit') {{
        state.transform.distance = defaultDistance;
      }} else if (preset === 'iso') {{
        state.transform.yaw = 0.7;
        state.transform.pitch = 0.4;
        state.transform.distance = defaultDistance;
      }} else if (preset === 'xy') {{
        state.transform.yaw = 0.0;
        state.transform.pitch = 0.0;
        state.transform.distance = defaultDistance;
      }} else if (preset === 'xy_rev') {{
        state.transform.yaw = Math.PI;
        state.transform.pitch = 0.0;
        state.transform.distance = defaultDistance;
      }} else if (preset === 'xz') {{
        state.transform.yaw = 0.0;
        state.transform.pitch = 1.55;
        state.transform.distance = defaultDistance;
      }} else if (preset === 'xz_rev') {{
        state.transform.yaw = 0.0;
        state.transform.pitch = -1.55;
        state.transform.distance = defaultDistance;
      }} else if (preset === 'yz') {{
        state.transform.yaw = Math.PI / 2.0;
        state.transform.pitch = 0.0;
        state.transform.distance = defaultDistance;
      }} else if (preset === 'yz_rev') {{
        state.transform.yaw = -Math.PI / 2.0;
        state.transform.pitch = 0.0;
        state.transform.distance = defaultDistance;
      }}
      pendingThreeCameraPreset = preset;
      drawViewer();
    }}

    function drawViewer() {{
      updateViewerEngineUI();
      if (state.viewerEngine === 'three' && window.LeakageThreeViewer) {{
        syncThreeViewer();
        return;
      }}
      drawViewerOn(fullCanvas, 'full');
      drawViewerOn(roiCanvas, 'roi');
    }}

    function initViewerInteraction() {{
      let dragging = false;
      let px = 0;
      let py = 0;
      let totalMove = 0;
      function shouldUseDragSelection(ev) {{
        return state.gapSelectionMethod === 'drag_box'
          && (state.gapTargetMode === 'component_move_gap' || state.gapTargetMode === 'face_gap')
          && !ev.shiftKey;
      }}
      function beginSelectionBox(ev, mode, canvas) {{
        const rect = canvas.getBoundingClientRect();
        state.selectionBox = {{
          active: true,
          additive: !!(ev.ctrlKey || ev.metaKey),
          canvasMode: mode,
          startX: ev.clientX - rect.left,
          startY: ev.clientY - rect.top,
          currentX: ev.clientX - rect.left,
          currentY: ev.clientY - rect.top
        }};
        drawViewer();
      }}
      function startDrag(ev, mode, canvas) {{
        if (shouldUseDragSelection(ev)) {{
          dragging = false;
          totalMove = 0;
          beginSelectionBox(ev, mode, canvas);
          return;
        }}
        dragging = true;
        px = ev.clientX;
        py = ev.clientY;
        totalMove = 0;
      }}
      fullCanvas.addEventListener('mousedown', function (ev) {{ startDrag(ev, 'full', fullCanvas); }});
      roiCanvas.addEventListener('mousedown', function (ev) {{ startDrag(ev, 'roi', roiCanvas); }});
      window.addEventListener('mouseup', function (ev) {{
        dragging = false;
        if (state.selectionBox.active) {{
          const dx = state.selectionBox.currentX - state.selectionBox.startX;
          const dy = state.selectionBox.currentY - state.selectionBox.startY;
          const boxSize = Math.abs(dx) + Math.abs(dy);
          const mode = state.selectionBox.canvasMode;
          const canvas = mode === 'roi' ? roiCanvas : fullCanvas;
          if (boxSize > 8) {{
            const rect = {{
              x0: state.selectionBox.startX,
              y0: state.selectionBox.startY,
              x1: state.selectionBox.currentX,
              y1: state.selectionBox.currentY
            }};
            if (state.gapTargetMode === 'component_move_gap') {{
              selectGapComponentsInRect(canvas, mode, rect, state.selectionBox.additive);
            }} else if (state.gapTargetMode === 'face_gap') {{
              selectLocalGapFacesInRect(canvas, mode, rect, state.selectionBox.additive);
            }}
          }}
          state.selectionBox.active = false;
          drawViewer();
        }}
      }});
      window.addEventListener('mousemove', function (ev) {{
        if (state.selectionBox.active) {{
          const canvas = state.selectionBox.canvasMode === 'roi' ? roiCanvas : fullCanvas;
          const rect = canvas.getBoundingClientRect();
          state.selectionBox.currentX = ev.clientX - rect.left;
          state.selectionBox.currentY = ev.clientY - rect.top;
          drawViewer();
          return;
        }}
        if (!dragging) return;
        const dx = ev.clientX - px;
        const dy = ev.clientY - py;
        px = ev.clientX;
        py = ev.clientY;
        totalMove += Math.abs(dx) + Math.abs(dy);
        state.transform.yaw += dx * 0.005;
        state.transform.pitch += dy * 0.005;
        state.transform.pitch = Math.max(-1.55, Math.min(1.55, state.transform.pitch));
        drawViewer();
      }});
      function handleWheel(ev) {{
        ev.preventDefault();
        state.transform.distance *= ev.deltaY > 0 ? 1.08 : 0.92;
        state.transform.distance = Math.max(0.4, Math.min(6.5, state.transform.distance));
        drawViewer();
      }}
      fullCanvas.addEventListener('wheel', handleWheel, {{ passive: false }});
      roiCanvas.addEventListener('wheel', handleWheel, {{ passive: false }});

      function handlePick(ev, mode) {{
        if (state.selectionBox.active) return;
        if (totalMove > 6) return;
        const canvas = mode === 'roi' ? roiCanvas : fullCanvas;
        const faceIndex = pickFaceFromCanvas(canvas, mode, ev.clientX, ev.clientY);
        handleViewerPickFace(faceIndex, mode, ev);
      }}

      function handleComponentContextPick(ev, mode) {{
        ev.preventDefault();
        ev.stopPropagation();
        if (totalMove > 6) return;
        const canvas = mode === 'roi' ? roiCanvas : fullCanvas;
        const faceIndex = pickFaceFromCanvas(canvas, mode, ev.clientX, ev.clientY);
        showComponentContextMenuForFace(faceIndex, ev.clientX, ev.clientY);
      }}

      fullCanvas.addEventListener('click', function (ev) {{
        handlePick(ev, 'full');
      }});
      roiCanvas.addEventListener('click', function (ev) {{
        handlePick(ev, 'roi');
      }});
      fullCanvas.addEventListener('contextmenu', function (ev) {{
        handleComponentContextPick(ev, 'full');
      }});
      roiCanvas.addEventListener('contextmenu', function (ev) {{
        handleComponentContextPick(ev, 'roi');
      }});
      viewerWrap.addEventListener('leakage-three-pick', function (ev) {{
        const detail = ev.detail || {{}};
        handleViewerPickFace(detail.faceIndex, detail.mode || 'full', detail);
      }});
      viewerWrap.addEventListener('leakage-three-contextmenu', function (ev) {{
        const detail = ev.detail || {{}};
        showComponentContextMenuForFace(detail.faceIndex, detail.clientX, detail.clientY);
      }});
    }}

    roiInput.addEventListener('input', function () {{
      state.panelFaces = new Set(parseFaceList(roiInput.value));
      state.selectedObjectIds.clear();
      recomputeSelectedFaces();
    }});

    importCadBtn.addEventListener('click', function () {{
      cadFilePicker.click();
    }});
    sidebarNavShell.addEventListener('click', function (ev) {{
      const target = ev.target.closest('[data-side-tab]');
      if (!target) return;
      switchSideTab(target.getAttribute('data-side-tab'));
    }});
    sidebarLayoutToggle.addEventListener('click', function (ev) {{
      const target = ev.target.closest('[data-layout]');
      if (!target) return;
      setSidebarLayout(target.getAttribute('data-layout'));
    }});
    loadDemoCadBtn.addEventListener('click', function () {{
      cadInput.value = demoCadPath;
      cadFileName.value = demoCadName;
      cadMeta.textContent = 'Demo CAD selected.';
      loadScene();
    }});
    cadFilePicker.addEventListener('change', async function () {{
      if (!cadFilePicker.files || !cadFilePicker.files.length) return;
      await uploadCadFile(cadFilePicker.files[0]);
      cadFilePicker.value = '';
    }});
    useSampleBtn.addEventListener('click', function () {{
      cadInput.value = '';
      cadFileName.value = 'Sample geometry (no CAD file)';
      cadMeta.textContent = 'Sample geometry selected.';
      loadScene();
    }});
    clearRoiBtn.addEventListener('click', function () {{
      resetRoiSelection();
      recomputeSelectedFaces();
    }});
    roiSelectionMode.addEventListener('change', function () {{
      state.roiSelectionMode = roiSelectionMode.value;
      updateSelectionModeUI();
      recomputeSelectedFaces();
    }});
    gapTargetMode.addEventListener('change', function () {{
      state.gapTargetMode = gapTargetMode.value;
      updateGapModeUI();
      updateGapSelectionStats();
      drawViewer();
    }});
    gapSelectionMethod.addEventListener('change', function () {{
      state.gapSelectionMethod = gapSelectionMethod.value;
      updateSelectionModeUI();
      drawViewer();
    }});
    gapFaceInput.addEventListener('input', function () {{
      updateGapSelectionStats();
      drawViewer();
    }});
    viewerEngineGroup.addEventListener('click', function (ev) {{
      const target = ev.target.closest('[data-viewer-engine]');
      if (!target) return;
      state.viewerEngine = target.getAttribute('data-viewer-engine');
      updateViewerEngineUI();
      drawViewer();
    }});
    renderModeGroup.addEventListener('click', function (ev) {{
      const target = ev.target.closest('[data-render-mode]');
      if (!target) return;
      state.renderMode = target.getAttribute('data-render-mode');
      updateRenderModeUI();
      drawViewer();
    }});
    cameraPresetGroup.addEventListener('click', function (ev) {{
      const target = ev.target.closest('[data-camera]');
      if (!target) return;
      applyCameraPreset(target.getAttribute('data-camera'));
    }});
    openRayTraceResultBtn.addEventListener('click', function () {{
      if (state.rayTraceResult && lastRayTraceResultHtml) {{
        renderRayTraceResultPopup(lastRayTraceResultHtml, state.rayTraceResult);
      }}
    }});
    componentContextMenu.addEventListener('click', function (ev) {{
      const actionButton = ev.target.closest('[data-component-context-action]');
      if (!actionButton) return;
      ev.preventDefault();
      ev.stopPropagation();
      runComponentContextMenuAction(actionButton.getAttribute('data-component-context-action'));
    }});
    document.addEventListener('pointerdown', function (ev) {{
      if (componentContextMenu.classList.contains('hidden-block')) return;
      if (!componentContextMenu.contains(ev.target)) hideComponentContextMenu();
    }});
    document.addEventListener('keydown', function (ev) {{
      if (ev.key === 'Escape') hideComponentContextMenu();
    }});
    window.addEventListener('blur', hideComponentContextMenu);
    window.addEventListener('scroll', hideComponentContextMenu, true);
    rayTraceResultTabs.addEventListener('click', function (ev) {{
      const tabButton = ev.target.closest('[data-result-tab]');
      if (!tabButton) return;
      setRayTraceResultTab(tabButton.getAttribute('data-result-tab'));
    }});
    rayDisplayPanel.addEventListener('change', function (ev) {{
      const target = ev.target.closest('[data-ray-filter]');
      if (!target) return;
      const filterName = target.getAttribute('data-ray-filter');
      if (!(filterName in state.rayDisplayFilters)) return;
      state.rayDisplayFilters[filterName] = !!target.checked;
      updateRayDisplayUI();
      drawViewer();
    }});
    rayReceiverOnlyBtn.addEventListener('click', function () {{ applyRayDisplayPreset('receiver'); }});
    rayAllOnBtn.addEventListener('click', function () {{ applyRayDisplayPreset('all'); }});
    rayAllOffBtn.addEventListener('click', function () {{ applyRayDisplayPreset('none'); }});
    function bindMoveInput(inputElX, inputElY, inputElZ) {{
      if (!inputElX || !inputElY || !inputElZ) return;
      const handler = function () {{
        setGapMoveVector(
          inputElX.value,
          inputElY.value,
          inputElZ.value
        );
      }};
      inputElX.addEventListener('input', handler);
      inputElY.addEventListener('input', handler);
      inputElZ.addEventListener('input', handler);
    }}
    function bindTiltInput(inputElX, inputElY, inputElZ) {{
      if (!inputElX || !inputElY || !inputElZ) return;
      const handler = function () {{
        setGapTiltVector(
          inputElX.value,
          inputElY.value,
          inputElZ.value
        );
      }};
      inputElX.addEventListener('input', handler);
      inputElY.addEventListener('input', handler);
      inputElZ.addEventListener('input', handler);
    }}
    bindMoveInput(gapMoveX, gapMoveY, gapMoveZ);
    bindMoveInput(popupMoveX, popupMoveY, popupMoveZ);
    bindMoveInput(cursorMoveX, cursorMoveY, cursorMoveZ);
    bindTiltInput(gapTiltX, gapTiltY, gapTiltZ);
    bindTiltInput(popupTiltX, popupTiltY, popupTiltZ);
    bindTiltInput(cursorTiltX, cursorTiltY, cursorTiltZ);
    cursorMoveClose.addEventListener('click', hideMovePopup);
    if (cursorMaterialClose) {{
      cursorMaterialClose.addEventListener('click', hideMaterialPopup);
    }}
    if (cursorMaterialOpenLibrary) {{
      cursorMaterialOpenLibrary.addEventListener('click', function () {{
        switchSideTab('material', {{ forceOpen: true }});
      }});
    }}
    cursorMovePopupHeader.addEventListener('mousedown', startPopupDrag);
    if (cursorMaterialPopupHeader) {{
      cursorMaterialPopupHeader.addEventListener('mousedown', startMaterialPopupDrag);
    }}
    if (cursorEmitterPopupHeader) {{
      cursorEmitterPopupHeader.addEventListener('mousedown', startEmitterPopupDrag);
    }}
    if (cursorReceiverPopupHeader) {{
      cursorReceiverPopupHeader.addEventListener('mousedown', startReceiverPopupDrag);
    }}
    rayTraceResultPopupHeader.addEventListener('mousedown', startRayTraceResultPopupDrag);
    rayTraceResultResizeHandle.addEventListener('mousedown', startRayTraceResultPopupResize);
    rayTraceResultPopupClose.addEventListener('click', hideRayTraceResultPopup);
    window.addEventListener('mousemove', movePopupDrag);
    window.addEventListener('mousemove', moveMaterialPopupDrag);
    window.addEventListener('mousemove', moveEmitterPopupDrag);
    window.addEventListener('mousemove', moveReceiverPopupDrag);
    window.addEventListener('mousemove', moveRayTraceResultPopupDrag);
    window.addEventListener('mousemove', moveRayTraceResultPopupResize);
    window.addEventListener('mouseup', stopPopupDrag);
    window.addEventListener('mouseup', stopMaterialPopupDrag);
    window.addEventListener('mouseup', stopEmitterPopupDrag);
    window.addEventListener('mouseup', stopReceiverPopupDrag);
    window.addEventListener('mouseup', stopRayTraceResultPopupDrag);
    window.addEventListener('mouseup', stopRayTraceResultPopupResize);
    window.addEventListener('mouseleave', stopPopupDrag);
    window.addEventListener('mouseleave', stopMaterialPopupDrag);
    window.addEventListener('mouseleave', stopEmitterPopupDrag);
    window.addEventListener('mouseleave', stopReceiverPopupDrag);
    window.addEventListener('mouseleave', stopRayTraceResultPopupDrag);
    window.addEventListener('mouseleave', stopRayTraceResultPopupResize);
    previewOverlayToggle.addEventListener('change', function () {{
      state.previewOverlayEnabled = !!previewOverlayToggle.checked;
      drawViewer();
    }});
    if (cursorApplyBtn) {{
      cursorApplyBtn.addEventListener('click', applyActiveTransformPreview);
    }}
    if (cursorResetBtn) {{
      cursorResetBtn.addEventListener('click', resetTransformEditorInputs);
    }}
    if (cursorRestoreBtn) {{
      cursorRestoreBtn.addEventListener('click', restoreActiveTransformOriginal);
    }}
    if (materialTargetMode) {{
      materialTargetMode.addEventListener('change', function () {{
        state.materialTargetMode = materialTargetMode.value || 'part';
        updateMaterialTargetSummary();
      }});
    }}
    if (materialPopupBaseSelect) {{
      materialPopupBaseSelect.addEventListener('change', function () {{
        state.materialDraft.base_material_id = materialPopupBaseSelect.value;
        if (!state.materialDraft.surface_id) {{
          state.materialDraft.surface_id = defaultSurfaceIdForBase(materialPopupBaseSelect.value);
        }}
        state.materialDraft.profile_id = '';
        renderMaterialLibrary();
        updateMaterialTargetSummary();
      }});
    }}
    if (materialPopupSurfaceSelect) {{
      materialPopupSurfaceSelect.addEventListener('change', function () {{
        state.materialDraft.surface_id = materialPopupSurfaceSelect.value;
        state.materialDraft.profile_id = '';
        renderMaterialLibrary();
        updateMaterialTargetSummary();
      }});
    }}
    if (materialPopupProfileSelect) {{
      materialPopupProfileSelect.addEventListener('change', function () {{
        const profileId = materialPopupProfileSelect.value || '';
        state.materialDraft.profile_id = profileId;
        const profile = getMaterialProfileById(profileId);
        if (profile) {{
          state.materialDraft.base_material_id = profile.base_material_id;
          state.materialDraft.surface_id = profile.surface_id;
          state.materialDraft.bsdf_asset_id = profile.bsdf_asset_id || '';
        }}
        renderMaterialLibrary();
        updateMaterialTargetSummary();
      }});
    }}
    if (materialApplyBtn) {{
      materialApplyBtn.addEventListener('click', function () {{
        applyMaterialAssignment(state.materialTargetMode || 'part');
      }});
    }}
    if (materialApplyFacesBtn) {{
      materialApplyFacesBtn.addEventListener('click', function () {{
        state.materialTargetMode = 'faces';
        if (materialTargetMode) materialTargetMode.value = 'faces';
        applyMaterialAssignment('faces');
      }});
    }}
    if (materialSaveProfileBtn) {{
      materialSaveProfileBtn.addEventListener('click', saveCurrentMaterialProfile);
    }}
    if (newMaterialBtn) {{
      newMaterialBtn.addEventListener('click', function (ev) {{
        ev.preventDefault();
        showMaterialLibraryForm('material');
      }});
    }}
    if (saveNewMaterialBtn) {{
      saveNewMaterialBtn.addEventListener('click', registerCustomMaterial);
    }}
    if (cancelNewMaterialBtn) {{
      cancelNewMaterialBtn.addEventListener('click', hideMaterialLibraryForms);
    }}
    if (newSurfaceBtn) {{
      newSurfaceBtn.addEventListener('click', function (ev) {{
        ev.preventDefault();
        showMaterialLibraryForm('surface');
      }});
    }}
    if (registerCustomSurfaceBtn) {{
      registerCustomSurfaceBtn.addEventListener('click', registerCustomSurface);
    }}
    if (cancelNewSurfaceBtn) {{
      cancelNewSurfaceBtn.addEventListener('click', hideMaterialLibraryForms);
    }}
    if (newBsdfBtn) {{
      newBsdfBtn.addEventListener('click', function (ev) {{
        ev.preventDefault();
        showMaterialLibraryForm('bsdf');
      }});
    }}
    if (bsdfFileInput) {{
      bsdfFileInput.addEventListener('change', function () {{
        const file = bsdfFileInput.files && bsdfFileInput.files.length ? bsdfFileInput.files[0] : null;
        if (bsdfFileName) {{
          bsdfFileName.value = file ? file.name : 'No file selected';
        }}
      }});
    }}
    if (registerBsdfBtn) {{
      registerBsdfBtn.addEventListener('click', registerBsdfAsset);
    }}
    if (cancelNewBsdfBtn) {{
      cancelNewBsdfBtn.addEventListener('click', hideMaterialLibraryForms);
    }}
    addFaceEmitterBtn.addEventListener('click', beginFaceEmitterSelection);
    addDatumEmitterBtn.addEventListener('click', beginDatumEmitterCreation);
    addReferenceEmitterBtn.addEventListener('click', beginReferenceEmitterSelection);
    emitterAddSection.addEventListener('toggle', function () {{
      const hasUnsavedDraft = !currentEmitter() && !cursorEmitterPopup.classList.contains('hidden-block');
      if (!emitterAddSection.open && (state.emitterSelectionActive || hasUnsavedDraft)) {{
        cancelEmitterSelection();
      }}
    }});
    cursorEmitterClose.addEventListener('click', function () {{
      cancelEmitterSelection();
      hideEmitterPopup();
    }});
    emitterReselectFacesBtn.addEventListener('click', reselectEmitterGeometry);
    emitterApplyBtn.addEventListener('click', applyEmitterEditor);
    emitterResetBtn.addEventListener('click', resetEmitterProperties);
    emitterDeleteBtn.addEventListener('click', deleteCurrentEmitter);
    emitterClearReferencesBtn.addEventListener('click', clearEmitterReferenceSelection);
    emitterDistributionSelect.addEventListener('change', function () {{
      updateEmitterDistributionUI();
      drawViewer();
    }});
    emitterPowerModeSelect.addEventListener('change', function () {{
      updateEmitterPowerUI();
      updateEmitterDraftSummary();
    }});
    emitterReferenceModeSelect.addEventListener('change', function () {{
      clearEmitterReferenceSelection();
    }});
    emitterReferenceSurfaceSelect.addEventListener('change', function () {{
      updateEmitterDraftSummary();
      updateEmitterSelectionUI();
      drawViewer();
    }});
    for (const input of [
      emitterCenterX, emitterCenterY, emitterCenterZ,
      emitterWidthInput, emitterHeightInput,
      emitterRotationX, emitterRotationY, emitterRotationZ
    ]) {{
      input.addEventListener('input', function () {{
        updateEmitterDraftSummary();
        drawViewer();
      }});
    }}
    emitterNormalFlipInput.addEventListener('change', function () {{
      updateEmitterDraftSummary();
      drawViewer();
    }});
    addDatumReceiverBtn.addEventListener('click', function () {{ beginReceiverCreation('datum_plane'); }});
    addReferenceReceiverBtn.addEventListener('click', function () {{ beginReceiverCreation('reference_plane'); }});
    addCurrentViewReceiverBtn.addEventListener('click', function () {{ beginReceiverCreation('current_view'); }});
    receiverAddSection.addEventListener('toggle', function () {{
      const hasUnsavedDraft = !currentReceiver() && !cursorReceiverPopup.classList.contains('hidden-block');
      if (!receiverAddSection.open && (state.receiverSelectionActive || hasUnsavedDraft)) {{
        cancelReceiverSelection();
      }}
    }});
    cursorReceiverClose.addEventListener('click', function () {{
      cancelReceiverSelection();
      hideReceiverPopup();
    }});
    receiverReselectGeometryBtn.addEventListener('click', reselectReceiverGeometry);
    receiverApplyBtn.addEventListener('click', applyReceiverEditor);
    receiverResetBtn.addEventListener('click', resetReceiverEditorValues);
    receiverDeleteBtn.addEventListener('click', deleteCurrentReceiver);
    receiverClearReferencesBtn.addEventListener('click', clearReceiverReferenceSelection);
    receiverReferenceModeSelect.addEventListener('change', function () {{
      clearReceiverReferenceSelection();
    }});
    receiverCaptureViewBtn.addEventListener('click', function () {{
      captureCurrentViewReceiverPlane();
      updateReceiverDraftSummary();
      drawViewer();
    }});
    for (const input of [
      receiverCenterX, receiverCenterY, receiverCenterZ,
      receiverWidthInput, receiverHeightInput,
      receiverRotationX, receiverRotationY, receiverRotationZ,
      receiverOffsetX, receiverOffsetY, receiverOffsetZ,
      receiverTiltX, receiverTiltY, receiverTiltZ,
      receiverViewDistanceInput, receiverResolutionX, receiverResolutionY,
      receiverAcceptanceInput
    ]) {{
      input.addEventListener('input', function () {{
        if (input === receiverViewDistanceInput && state.receiverDraftType === 'current_view') {{
          captureCurrentViewReceiverPlane();
        }}
        updateReceiverDraftSummary();
        drawViewer();
      }});
    }}
    receiverNormalFlipInput.addEventListener('change', function () {{
      updateReceiverDraftSummary();
      drawViewer();
    }});
    axisScale.addEventListener('input', function () {{
      state.axisScalePercent = parseInt(axisScale.value, 10) || 100;
      axisScaleValue.textContent = state.axisScalePercent + '%';
      drawViewer();
    }});
    window.addEventListener('leakage-three-ready', function () {{
      updateViewerEngineUI();
      drawViewer();
    }});
    runForm.addEventListener('keydown', function (ev) {{
      if (ev.key === 'F2') {{
        if (state.selectedGapObjectId !== null && state.selectedGapObjectId !== undefined) {{
          ev.preventDefault();
          beginRenameComponent(state.selectedGapObjectId);
        }}
        return;
      }}
      if (ev.key !== 'Enter') return;
      const target = ev.target;
      if (!target || target.tagName === 'TEXTAREA') return;
      ev.preventDefault();
      if (target.closest && target.closest('#cursorEmitterPopup')) {{
        applyEmitterEditor();
        return;
      }}
      if (target.closest && target.closest('#cursorReceiverPopup')) {{
        applyReceiverEditor();
        return;
      }}
      if (target.closest && target.closest('#cursorMaterialPopup')) {{
        applyMaterialAssignment(state.materialTargetMode);
        return;
      }}
      if (target === cursorMoveX || target === cursorMoveY || target === cursorMoveZ
        || target === cursorTiltX || target === cursorTiltY || target === cursorTiltZ
        || target === gapMoveX || target === gapMoveY || target === gapMoveZ
        || target === gapTiltX || target === gapTiltY || target === gapTiltZ
        || target === popupMoveX || target === popupMoveY || target === popupMoveZ
        || target === popupTiltX || target === popupTiltY || target === popupTiltZ) {{
        applyActiveTransformPreview();
        return;
      }}
      if (typeof target.blur === 'function') {{
        target.blur();
      }}
      updateGapSelectionStats();
      drawViewer();
    }});
    runForm.addEventListener('submit', async function (ev) {{
      ev.preventDefault();
      await runDirectRayTrace();
    }});

    window.addEventListener('resize', function () {{
      hideComponentContextMenu();
      drawViewer();
      if (!rayTraceResultPopup.classList.contains('hidden-block') && state.resultPopupPosition) {{
        applyRayTraceResultPopupPosition(state.resultPopupPosition.left, state.resultPopupPosition.top);
      }}
    }});
    renderEmitterList();
    renderReceiverList();
    setSidebarLayout('vertical');
    ensureMaterialLibraryState();
    renderMaterialLibrary();
    state.gapSelectionMethod = gapSelectionMethod.value;
    updateSelectionModeUI();
    updateGapModeUI();
    syncTransformInputs();
    updateGapSelectionStats();
    updateMaterialTargetSummary();
    updateRayTraceRunState();
    updateRayDisplayUI();
    state.renderMode = 'wireframe';
    state.axisScalePercent = parseInt(axisScale.value, 10) || 100;
    state.previewOverlayEnabled = !!previewOverlayToggle.checked;
    axisScaleValue.textContent = state.axisScalePercent + '%';
    updateRenderModeUI();
    initViewerInteraction();
    updateViewerMode();
    initDevAutoRefresh();
    loadScene();
  </script>
</body>
</html>"""


def _build_custom_emitter(data: Dict[str, str]) -> Optional[EmitterConfig]:
    emitter_type = data.get("emitter_type", "").strip()
    if not emitter_type:
        return None
    return EmitterConfig(
        source_id="web_custom_emitter",
        emitter_type=emitter_type,
        strength=_parse_float(data.get("emitter_strength", "1.0"), 1.0),
        direction_mode="toward_receiver",
        direction_distribution=data.get("emitter_direction_distribution", "isotropic"),
        face_index=_parse_int(data.get("emitter_face_index", "")),
        normal_hint=_parse_tuple(data.get("emitter_normal_hint", "")),
        box_min=_parse_tuple(data.get("emitter_box_min", "")),
        box_max=_parse_tuple(data.get("emitter_box_max", "")),
        sphere_center=_parse_tuple(data.get("emitter_sphere_center", "")),
        sphere_radius=_parse_float(data.get("emitter_sphere_radius", ""), None),
    )


def _parse_material_override(data: Dict[str, str]) -> Optional[Dict[str, float]]:
    mapping = {
        "reflectance_total": "material_reflectance",
        "diffuse_ratio": "material_diffuse",
        "specular_ratio": "material_specular",
        "roughness": "material_roughness",
        "absorption_ratio": "material_absorption",
        "alpha": "material_alpha",
    }
    out: Dict[str, float] = {}
    for out_key, field in mapping.items():
        value = data.get(field, "").strip()
        if value:
            out[out_key] = _parse_float(value, None)
    return out or None


def _pick(form_data: Dict[str, List[str]], key: str, default: str = "") -> str:
    value = form_data.get(key)
    if not value:
        return default
    return value[0]


class LeakageWebHandler(BaseHTTPRequestHandler):
    server_version = "LeakageSimWeb/{}".format(WEB_UI_VERSION)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._send_html(200, _build_html_form(_material_library_options(), WEB_UI_VERSION))
            return

        if parsed.path == "/api/scene":
            params = urllib.parse.parse_qs(parsed.query)
            cad = params.get("cad", [""])[0]
            try:
                payload = build_scene_payload(cad)
                payload.setdefault("metadata", {})["scene_token"] = _cache_scene_mesh(payload)
                self._send_json(200, payload)
            except Exception as exc:
                self._send_json(500, {{"error": str(exc)}})
            return

        if parsed.path == "/health":
            self._send_plain(200, "ok web_ui_version={}".format(WEB_UI_VERSION))
            return

        if parsed.path == "/dev-status":
            self._send_json(
                200,
                {
                    "ok": True,
                    "web_ui_version": WEB_UI_VERSION,
                    "boot_token": SERVER_BOOT_TOKEN,
                },
            )
            return

        if parsed.path == "/_ping":
            self._send_plain(200, "pong")
            return

        if parsed.path == "/api/raytrace/status":
            params = urllib.parse.parse_qs(parsed.query)
            job_id = str(params.get("job_id", [""])[0])
            job = _raytrace_job_snapshot(job_id)
            if job is None:
                self._send_json(404, {"error": "Ray tracing job was not found"})
            else:
                self._send_json(200, job)
            return

        if parsed.path.startswith("/static/"):
            rel = parsed.path[len("/static/") :]
            path = _safe_static_path(rel)
            if path is None:
                self._send_plain(404, "Not found")
                return
            self._send_file(path, _static_mime(path))
            return

        if parsed.path.startswith("/outputs/"):
            rel = parsed.path[len("/outputs/") :]
            if not rel or rel.endswith("/") or "/" in rel:
                self._send_plain(404, "Not found")
                return
            output_name = _safe_output_name(rel)
            if output_name is None:
                self._send_plain(400, "Invalid file name")
                return
            path = OUTPUT_FILE_INDEX.get(output_name)
            if path is None:
                path = ROOT / "outputs" / output_name
            if not path.exists():
                self._send_plain(404, "Not found")
                return
            if output_name.lower().endswith(".png"):
                self._send_file(path, "image/png")
                return
            if output_name.lower().endswith(".html"):
                self._send_html(200, path.read_text(encoding="utf-8"))
                return
            if output_name.lower().endswith(".json") or output_name.lower().endswith(".csv"):
                self._send_plain(200, path.read_text(encoding="utf-8"))
                return
            self._send_file(path, "application/octet-stream")
            return

        self._send_plain(404, "Not found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/api/raytrace/start":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    raise ValueError("Ray tracing request is empty")
                request_payload = json.loads(self.rfile.read(length).decode("utf-8"))
                scene_token = str(request_payload.get("scene_token") or "")
                scene_mesh = SCENE_MESH_CACHE.get(scene_token)
                if scene_mesh is None:
                    raise ValueError("CAD scene cache expired. Reload the CAD model and run again")
                requested_ray_count = sum(
                    max(0, int(emitter.get("ray_count", 0)))
                    for emitter in request_payload.get("emitters", [])
                    if emitter.get("enabled", True)
                )
                job_id = uuid.uuid4().hex
                job = {
                    "job_id": job_id,
                    "status": "queued",
                    "phase": "queued",
                    "processed_rays": 0,
                    "total_rays": requested_ray_count,
                    "progress": 0.0,
                    "elapsed_sec": 0.0,
                    "estimated_remaining_sec": None,
                    "rays_per_sec": 0.0,
                    "created_at": time.time(),
                }
                with RAYTRACE_JOBS_LOCK:
                    RAYTRACE_JOBS[job_id] = job
                _prune_raytrace_jobs()
                worker = threading.Thread(
                    target=_run_raytrace_job,
                    args=(job_id, scene_mesh, request_payload),
                    daemon=True,
                    name="raytrace-{}".format(job_id[:8]),
                )
                worker.start()
                self._send_json(202, job)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if parsed.path == "/api/raytrace/direct":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    raise ValueError("Direct ray tracing request is empty")
                request_payload = json.loads(self.rfile.read(length).decode("utf-8"))
                scene_token = str(request_payload.get("scene_token") or "")
                scene_mesh = SCENE_MESH_CACHE.get(scene_token)
                if scene_mesh is None:
                    raise ValueError("CAD scene cache expired. Reload the CAD model and run again")
                trace_input = build_direct_trace_input(scene_mesh, request_payload)
                result = run_direct_ray_trace(trace_input)
                self._send_json(200, result.to_dict())
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if parsed.path == "/api/upload":
            params = urllib.parse.parse_qs(parsed.query)
            raw_name = params.get("filename", [""])[0]
            try:
                target_path, display_name = _prepare_upload_path(raw_name)
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    raise ValueError("No file content received")
                data = self.rfile.read(length)
                if not data:
                    raise ValueError("Uploaded file is empty")
                target_path.write_bytes(data)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "display_name": display_name,
                        "path": str(target_path),
                    },
                )
            except Exception as exc:
                self._send_plain(400, "Upload failed: {}".format(exc))
            return

        if parsed.path != "/run":
            self._send_plain(404, "Not found")
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        data = parse_qs(raw)

        try:
            config = RunConfig(
                ray_count=int(_pick(data, "rays", "4000")),
                max_depth=int(_pick(data, "max_depth", "2")),
                seed=int(_pick(data, "seed", "42")),
                k_abs=float(_pick(data, "k_abs", "0.12")),
                k_brdf=float(_pick(data, "k_brdf", "1.0")),
            )
            roi_faces = _parse_int_list(_pick(data, "roi_faces", ""))
            gap_mode = _pick(data, "gap_mode", "component_move_gap")
            selected_gap_components = _parse_int_list(_pick(data, "gap_component_ids", "")) or []
            selected_gap_faces = _parse_int_list(_pick(data, "gap_face_indices", "")) or []
            gap = None
            if gap_mode == "component_move_gap" and selected_gap_components:
                gap = GapRule(
                    rule_id="web_gap",
                    target_face_indices=[],
                    nominal_gap_mm=_parse_float(_pick(data, "gap_nominal", "0.0"), 0.0),
                    sigma_gap_mm=_parse_float(_pick(data, "gap_sigma", "0.03"), 0.03),
                    enable_tunnel=True,
                    transmissive_threshold=_parse_float(_pick(data, "gap_transmissive_threshold", "0.4"), 0.4),
                    gap_mode="component_move_gap",
                    target_component_ids=selected_gap_components,
                    move_vector_mm=_parse_tuple(_pick(data, "gap_move_xyz", "")) or (0.0, 0.0, 0.0),
                    rotation_vector_deg=_parse_tuple(_pick(data, "gap_tilt_xyz", "")) or (0.0, 0.0, 0.0),
                )
            elif gap_mode == "face_gap" and selected_gap_faces:
                gap = GapRule(
                    rule_id="web_gap",
                    target_face_indices=selected_gap_faces,
                    nominal_gap_mm=_parse_float(_pick(data, "gap_nominal", "0.0"), 0.0),
                    sigma_gap_mm=_parse_float(_pick(data, "gap_sigma", "0.03"), 0.03),
                    enable_tunnel=True,
                    transmissive_threshold=_parse_float(_pick(data, "gap_transmissive_threshold", "0.4"), 0.4),
                    gap_mode="face_gap",
                    move_vector_mm=_parse_tuple(_pick(data, "gap_move_xyz", "")) or (0.0, 0.0, 0.0),
                    rotation_vector_deg=_parse_tuple(_pick(data, "gap_tilt_xyz", "")) or (0.0, 0.0, 0.0),
                )
            emitter = _build_custom_emitter({k: _pick(data, k, "") for k in data})
            result = execute_run(
                input_cad=_pick(data, "cad", None) or None,
                output_dir=_pick(data, "output_dir", "outputs"),
                run_config=config,
                gaps=[gap] if gap is not None else [],
                roi_face_indices=roi_faces,
                emitters=[emitter] if emitter else None,
                replace_emitters=(emitter is not None and _pick(data, "include_import_emitters") != "1"),
                material_preset_id=_pick(data, "material_preset"),
                material_override=_parse_material_override({k: _pick(data, k, "") for k in data}),
                auto_default_gap=False,
            )

            for path_name in [result.get("json"), result.get("csv"), result.get("heatmap"), result.get("report")]:
                _register_output_file(path_name)

            summary = result["summary"]
            hit_ratio = summary["hit_count"] / summary["total_rays"] if summary["total_rays"] else 0.0
            output_links = []
            for path_name in [result.get("json"), result.get("csv"), result.get("heatmap"), result.get("report")]:
                if path_name:
                    filename = Path(path_name).name
                    output_links.append("<li><a href=\"/outputs/{}\" target=\"_blank\">{}</a></li>".format(
                        html.escape(filename), html.escape(filename)
                    ))
            links_html = "".join(output_links)
            body = """
            <html><head><meta charset=\"utf-8\"/><title>Run result</title>
            <style>body{{font-family:Arial,Helvetica,sans-serif;padding:16px;}} .card{{border:1px solid #ddd;padding:8px;border-radius:8px;display:inline-block;margin:4px;}}</style>
            </head><body>
            <h2>Run complete (Web UI v{version})</h2>
            <p>Run ID: {run_id}</p>
            <div style=\"display:grid;grid-template-columns:repeat(4,1fr);max-width:920px;gap:8px;\">
              <div class=\"card\">total_rays: {total}</div>
              <div class=\"card\">hit_count: {hit}</div>
              <div class=\"card\">hit_ratio: {ratio:.2f}%</div>
              <div class=\"card\">runtime: {runtime:.2f}s</div>
            </div>
            <h3>Results</h3>
            <ul>{links}</ul>
            <p><a href=\"/\">Back</a></p>
            </body></html>
            """.format(
                version=WEB_UI_VERSION,
                run_id=html.escape(result["run_id"]),
                total=summary["total_rays"],
                hit=summary["hit_count"],
                ratio=hit_ratio * 100.0,
                runtime=summary["runtime_sec"],
                links=links_html,
            )
            self._send_html(200, body)
        except Exception:
            self._send_html(
                500,
                "<h3>Run failed</h3><pre>{}</pre><p><a href='/'>&lt;- Back</a></p>".format(
                    html.escape(traceback.format_exc())
                ),
            )

    def _send_json(self, code: int, payload: Dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, code: int, content: str) -> None:
        body = content.encode("utf-8")
        self.send_response(code)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_plain(self, code: int, content: str) -> None:
        body = content.encode("utf-8")
        self.send_response(code)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, mime: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", "inline; filename={}".format(path.name))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # noqa: A003
        return


def run_server(host: str = "127.0.0.1", port: int = 8787, strict_port: bool = False) -> None:
    start_port = port
    last_error: Optional[OSError] = None
    max_tries = 1 if strict_port else 24

    for trial in range(max_tries):
        candidate = start_port + trial
        try:
            httpd = ThreadingHTTPServer((host, candidate), LeakageWebHandler)
            script_path = str(Path(__file__).resolve())
            print("run web ui v{} at http://{}:{}".format(WEB_UI_VERSION, host, candidate))
            print("script: {}".format(script_path))
            print("pid: {}".format(os.getpid()))
            if candidate != start_port:
                print("port {} was occupied, fallback to {}".format(start_port, candidate))
            print("health: http://{}:{}/health".format(host, candidate))
            print("Press Ctrl + C to stop")
            httpd.serve_forever()
        except OSError as exc:
            last_error = exc
            if getattr(exc, "errno", None) not in (10013, 10048):
                raise
            if candidate + 1 - start_port >= max_tries:
                break
            print(
                "port {} is unavailable (errno={}); trying {}".format(
                    candidate,
                    exc.errno,
                    candidate + 1,
                )
            )
            continue

    raise RuntimeError(
        "failed to bind ports {}~{} (last_errno={})".format(
            start_port,
            start_port + max_tries - 1,
            getattr(last_error, "errno", None),
        )
    )


def main() -> None:
    port = 8787
    port_text = ""
    strict_port = "--strict-port" in sys.argv[1:]

    args = sys.argv[1:]
    for index, arg in enumerate(args):
        if arg == "--port" and index + 1 < len(args):
            port_text = args[index + 1].strip()
            break
        if arg.startswith("--port="):
            port_text = arg.split("=", 1)[1].strip()
            break

    if not port_text:
        port_text = os.environ.get("LEAKAGE_WEB_PORT", "").strip()

    if port_text:
        try:
            port = int(port_text)
        except ValueError:
            pass
    run_server(port=port, strict_port=strict_port)


if __name__ == "__main__":
    main()

