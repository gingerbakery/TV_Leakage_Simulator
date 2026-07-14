from __future__ import annotations

import html
import json
import re
import sys
import os
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent
sys.path.append(str(ROOT / "src"))

from leakage_simulator.engine import execute_run
from leakage_simulator.materials import default_material_library
from leakage_simulator.roi import build_scene_payload
from leakage_simulator.types import EmitterConfig, GapRule, RunConfig

WEB_UI_VERSION = "0.7.6"
OUTPUT_FILE_INDEX: Dict[str, Path] = {}
UPLOAD_DIR = ROOT / "_uploads"
DEMO_CAD_PATH = ROOT / "samples" / "demo_tv_frame.obj"
STATIC_DIR = ROOT / "web" / "static"
SERVER_BOOT_TOKEN = str(time.time_ns())


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
    body {{
      margin: 0;
      background: #f1f5f9;
      color: var(--ink);
      font-family: "Segoe UI", Arial, sans-serif;
    }}
    .app {{
      display: grid;
      grid-template-columns: 460px 1fr;
      min-height: 100vh;
    }}
    .panel {{
      background: white;
      border-right: 1px solid var(--line);
      padding: 16px;
      overflow: auto;
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
    .object-item {{ padding: 6px; border-bottom: 1px solid #e2e8f0; font-size: 13px; }}
    .object-item:last-child {{ border-bottom: none; }}
    .object-item.is-selected {{
      background: #dbeafe;
      border-radius: 10px;
    }}
    .component-tree-row {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      align-items: center;
    }}
    .component-row-main {{
      min-width: 0;
      cursor: pointer;
    }}
    .component-row-main .name {{
      font-size: 12px;
      font-weight: 700;
      color: #1e293b;
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
      gap: 6px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    .mini-btn {{
      padding: 6px 8px;
      border-radius: 8px;
      font-size: 11px;
      font-weight: 700;
    }}
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
      min-height: 100vh;
      color: #f8fafc;
    }}
    .viewer-inner {{
      height: calc(100vh - 52px);
      padding: 12px;
    }}
    .viewer-head {{
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
      left: 18px;
      bottom: 18px;
      z-index: 5;
      min-width: 220px;
      padding: 10px 12px;
      border-radius: 10px;
      background: rgba(15, 23, 42, 0.88);
      border: 1px solid rgba(148, 163, 184, 0.25);
      color: #e2e8f0;
      box-shadow: 0 8px 24px rgba(2, 6, 23, 0.25);
    }}
    .coord-badge .t {{
      font-size: 11px;
      color: #93c5fd;
      margin-bottom: 4px;
    }}
    .coord-badge .v {{
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
      height: calc(100vh - 145px);
      min-height: 520px;
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
            <button type=\"button\" class=\"side-tab-btn active\" data-side-tab=\"roi\">ROI 설정</button>
            <button type=\"button\" class=\"side-tab-btn\" data-side-tab=\"components\">Components</button>
            <button type=\"button\" class=\"side-tab-btn\" data-side-tab=\"transform_manager\">Transform manager</button>
            <button type=\"button\" class=\"side-tab-btn\" data-side-tab=\"material\">Material library</button>
            <button type=\"button\" class=\"side-tab-btn\" data-side-tab=\"raytracing\">Ray tracing</button>
            <button type=\"button\" class=\"side-tab-btn\" data-side-tab=\"result\">Result</button>
          </div>

          <div class=\"side-tab-panel active\" data-side-panel=\"roi\">
          <button type=\"button\" class=\"accordion-btn active\" data-side-tab=\"roi\">ROI 설정</button>
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
              Component row를 클릭하면 선택/해제되고, <b>Transform</b> 버튼을 누르면 오른쪽 3D viewer의 Transform popup이 열립니다.<br>
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
          <details>
            <summary>Information</summary>
            <div id=\"componentSelectionSummary\" class=\"move-summary\">선택된 부품 없음</div>
          </details>
          <label>Component Tree</label>
          <div id=\"gapObjectList\" class=\"object-list\">
            <div class=\"small\">Load CAD first</div>
          </div>
        </div>
          </div>
          </div>

          <div class=\"side-tab-panel\" data-side-panel=\"transform_manager\">
          <button type=\"button\" class=\"accordion-btn\" data-side-tab=\"transform_manager\">Transform manager</button>
          <div class=\"side-panel-body\">
          <div class=\"card\">
          <div class=\"step\">Step 4</div>
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
          <details>
            <summary>Information</summary>
            <div id=\"transformSelectionSummary\" class=\"move-summary\">Transform rule 없음. Components 탭에서 `Transform`을 눌러 시작하세요.</div>
            <div class=\"move-summary\" id=\"gapMoveSummary\">선택된 대상 없음</div>
          </details>
          <details>
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
            <div class=\"step\">Step 5</div>
            <h2>Material Library</h2>
            <div class=\"move-sub\">왼쪽은 라이브러리/등록/업로드용, 실제 부품 지정은 오른쪽 3D viewer의 Material popup에서 수행합니다.</div>
            <div id=\"materialTargetSummary\" class=\"move-summary\">선택된 material 대상 부품 없음</div>
            <details class=\"library-tree\" open>
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
            <details class=\"library-tree\" open>
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
                    <label>Reflectance<input id=\"customSurfaceReflectance\" type=\"number\" min=\"0\" max=\"1\" step=\"0.01\" value=\"0.12\"></label>
                    <label>Absorption<input id=\"customSurfaceAbsorption\" type=\"number\" min=\"0\" max=\"1\" step=\"0.01\" value=\"0.10\"></label>
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
          </div>
          </div>
          </div>

          <div class=\"side-tab-panel\" data-side-panel=\"raytracing\">
          <button type=\"button\" class=\"accordion-btn\" data-side-tab=\"raytracing\">Ray tracing</button>
          <div class=\"side-panel-body\">
          <div class=\"card\">
            <div class=\"step\">Step 6</div>
            <h2>Emitter</h2>
            <label>Type</label>
            <select id=\"emitterType\" name=\"emitter_type\">
              <option value=\"\" selected>Default (imported emitters)</option>
              <option value=\"face\">Face emitter</option>
              <option value=\"volume_box\">Volume box emitter</option>
              <option value=\"volume_sphere\">Volume sphere emitter</option>
            </select>
            <div class=\"grid\" style=\"margin-top: 6px;\">
              <label>Strength</label><input name=\"emitter_strength\" type=\"number\" step=\"0.1\" value=\"1.0\">
              <label>Direction distribution</label><input name=\"emitter_direction_distribution\" value=\"isotropic\">
            </div>
            <div class=\"grid\" style=\"margin-top: 6px;\">
              <label>Face index</label><input id=\"emitterFace\" name=\"emitter_face_index\" placeholder=\"ex: 0\">
              <label>Normal hint x,y,z</label><input id=\"emitterNormal\" name=\"emitter_normal_hint\" placeholder=\"ex: 0,0,1\">
            </div>
            <div class=\"grid\" style=\"margin-top: 6px;\">
              <label>Box min x,y,z</label><input name=\"emitter_box_min\" value=\"470,120,25\">
              <label>Box max x,y,z</label><input name=\"emitter_box_max\" value=\"520,180,45\">
            </div>
            <div class=\"grid\" style=\"margin-top: 6px;\">
              <label>Sphere center</label><input name=\"emitter_sphere_center\" value=\"250,150,40\">
              <label>Sphere radius</label><input name=\"emitter_sphere_radius\" type=\"number\" step=\"0.1\" value=\"20\">
            </div>
            <label><input id=\"includeImportEmitters\" type=\"checkbox\" name=\"include_import_emitters\" value=\"1\" checked> Include imported emitters</label>
          </div>

          <div class=\"card\">
            <div class=\"step\">Tracing setup</div>
            <h2>Ray tracing setup</h2>
            <div class=\"move-sub\">현재는 Run simulation을 잠시 꺼둔 상태입니다. Enter를 누르면 입력값만 반영됩니다.</div>
            <div class=\"grid\">
              <label>Ray count<input name=\"rays\" type=\"number\" value=\"4000\"></label>
              <label>Max depth<input name=\"max_depth\" type=\"number\" value=\"2\"></label>
              <label>Output folder<input name=\"output_dir\" value=\"outputs\"></label>
              <label>Seed<input name=\"seed\" type=\"number\" value=\"42\"></label>
            </div>
            <details>
              <summary>Advanced</summary>
              <div class=\"grid\" style=\"margin-top: 6px;\">
                <label>k_abs<input name=\"k_abs\" type=\"number\" step=\"0.01\" value=\"0.12\"></label>
                <label>k_brdf<input name=\"k_brdf\" type=\"number\" step=\"0.1\" value=\"1.0\"></label>
              </div>
            </details>
            <button id=\"runBtn\" class=\"run-btn\" type=\"button\" disabled>Run simulation (temporarily off)</button>
          </div>
          </div>
          </div>

          <div class=\"side-tab-panel\" data-side-panel=\"result\">
          <button type=\"button\" class=\"accordion-btn\" data-side-tab=\"result\">Result</button>
          <div class=\"side-panel-body\">
          <div class=\"card\">
            <div class=\"step\">Step 7</div>
            <h2>Result</h2>
            <div id=\"resultPlaceholder\" class=\"manager-empty\">Run simulation이 다시 활성화되면 결과 요약, before/after 비교, 리포트 링크가 이 구간에 표시됩니다.</div>
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
        <div id=\"viewerTip\" class=\"tip\">Drag = rotate, Wheel = zoom, Camera preset = 정면/측면 보기 고정.</div>
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
        </div>
        <div class=\"coord-badge\">
          <div class=\"t\">World coordinates</div>
          <div id=\"coordReadout\" class=\"v\">Origin: (0, 0, 0)</div>
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
      </div>
    </main>
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
        this.controls.enableDamping = true;
        this.controls.dampingFactor = 0.08;

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
        this.center = new THREE.Vector3(0, 0, 0);
        this.size = 1;
        this.renderMode = 'wireframe';
        this.axisScalePercent = 100;
        this.lastMeshRef = null;
        this.resizeObserver = new ResizeObserver(() => this.resize());
        this.resizeObserver.observe(this.container);
        this.animate = this.animate.bind(this);
        requestAnimationFrame(this.animate);
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
          if (child.geometry) child.geometry.dispose();
          if (child.material) {{
            if (Array.isArray(child.material)) {{
              child.material.forEach((mat) => mat.dispose());
            }} else {{
              child.material.dispose();
            }}
          }}
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
          if (!faceIndices.length) continue;
          const geometry = buildBufferGeometry(meshRef, faceIndices, {{ transformSpec: overlay }});
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
          this.overlayRoot.add(surface);

          const edges = new THREE.LineSegments(
            new THREE.EdgesGeometry(geometry, 18),
            new THREE.LineBasicMaterial({{
              color: overlay.edgeColor || overlay.color || 0xf87171,
              transparent: true,
              opacity: overlay.edgeOpacity ?? 0.95,
              depthTest: true,
            }})
          );
          edges.name = 'transform_edges_' + (overlay.kind || 'overlay');
          this.overlayRoot.add(edges);
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
    const state = {{
      mesh: null,
      selectedFaces: new Set(),
      clickedFaces: new Set(),
      panelFaces: new Set(),
      selectedObjectIds: new Set(),
      faceToObjectId: new Map(),
      selectedGapObjectId: null,
      selectedGapObjectIds: new Set(),
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
      activeSideTab: 'roi',
      openSidePanels: new Set(['roi']),
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
    const customSurfaceAbsorption = document.getElementById('customSurfaceAbsorption');
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

    function setResultMessage(text) {{
      if (resultPlaceholder) {{
        resultPlaceholder.style.display = 'none';
      }}
      resultPanel.style.display = 'block';
      resultPanel.innerHTML = text;
      switchSideTab('result', {{ forceOpen: true }});
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
      if (state.sidebarLayout === 'vertical' && !state.openSidePanels.size) {{
        state.openSidePanels.add(state.activeSideTab || 'roi');
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
      if (!item) return null;
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
        componentSelectionSummary.textContent = '선택된 부품 없음. Component row를 클릭하면 하이라이트되고, Transform 버튼을 누르면 오른쪽 viewer 입력창이 열립니다.';
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
        {{ material_id: 'black_powder_coated_aluminum', name: 'Black powder coated aluminum', category: 'metal', default_surface_id: 'black_powder_coat_fine' }},
        {{ material_id: 'black_pc_resin', name: 'Black PC resin', category: 'resin', default_surface_id: 'matte_black_resin' }},
        {{ material_id: 'anodized_aluminum', name: 'Anodized aluminum', category: 'metal', default_surface_id: 'anodized_matte' }},
        {{ material_id: 'matte_black_abs', name: 'Matte black ABS', category: 'resin', default_surface_id: 'matte_black_resin' }},
        {{ material_id: 'black_tape_general', name: 'Black tape', category: 'tape', default_surface_id: 'tape_black_matte' }},
        {{ material_id: 'foam_absorber_general', name: 'Foam absorber', category: 'foam', default_surface_id: 'foam_low_reflect' }}
      ];
    }}

    function buildInitialMaterialSurfaceLibrary() {{
      return [
        {{ surface_id: 'black_powder_coat_fine', name: 'Black powder coat - fine', scatter_model: 'gaussian', reflectance_total: 0.12, absorption_ratio: 0.10, roughness: 0.70, scatter_sigma_deg: 18 }},
        {{ surface_id: 'black_powder_coat_coarse', name: 'Black powder coat - coarse', scatter_model: 'gaussian', reflectance_total: 0.16, absorption_ratio: 0.08, roughness: 0.82, scatter_sigma_deg: 28 }},
        {{ surface_id: 'matte_black_resin', name: 'Matte black resin', scatter_model: 'lambertian', reflectance_total: 0.08, absorption_ratio: 0.18, roughness: 0.88, scatter_sigma_deg: 32 }},
        {{ surface_id: 'semi_gloss_black_resin', name: 'Semi-gloss black resin', scatter_model: 'mixed', reflectance_total: 0.10, absorption_ratio: 0.14, roughness: 0.45, scatter_sigma_deg: 14 }},
        {{ surface_id: 'anodized_matte', name: 'Anodized matte', scatter_model: 'mixed', reflectance_total: 0.18, absorption_ratio: 0.05, roughness: 0.50, scatter_sigma_deg: 12 }},
        {{ surface_id: 'tape_black_matte', name: 'Black tape matte', scatter_model: 'lambertian', reflectance_total: 0.05, absorption_ratio: 0.25, roughness: 0.92, scatter_sigma_deg: 38 }},
        {{ surface_id: 'foam_low_reflect', name: 'Foam low reflect', scatter_model: 'lambertian', reflectance_total: 0.03, absorption_ratio: 0.40, roughness: 0.98, scatter_sigma_deg: 45 }},
        {{ surface_id: 'corrosion_light', name: 'Corrosion - light', scatter_model: 'gaussian', reflectance_total: 0.14, absorption_ratio: 0.08, roughness: 0.76, scatter_sigma_deg: 24 }},
        {{ surface_id: 'corrosion_medium', name: 'Corrosion - medium', scatter_model: 'gaussian', reflectance_total: 0.18, absorption_ratio: 0.07, roughness: 0.84, scatter_sigma_deg: 34 }},
        {{ surface_id: 'corrosion_heavy', name: 'Corrosion - heavy', scatter_model: 'gaussian', reflectance_total: 0.22, absorption_ratio: 0.06, roughness: 0.94, scatter_sigma_deg: 46 }}
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
          + '<div class=\"meta\">category: ' + item.category + '\\ndefault surface: ' + item.default_surface_id + '</div>'
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
          + '<div class=\"meta\">scatter: ' + item.scatter_model + '\\nreflectance: ' + Number(item.reflectance_total).toFixed(2) + ' / roughness: ' + Number(item.roughness).toFixed(2) + '</div>'
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
      const defaultSurfaceId = String(newMaterialDefaultSurface.value || '').trim() || 'matte_black_resin';
      const materialId = 'material_' + name.toLowerCase().replace(/[^a-z0-9]+/g, '_') + '_' + Date.now();
      state.materialBaseLibrary.unshift({{
        material_id: materialId,
        name: name,
        category: category,
        default_surface_id: defaultSurfaceId
      }});
      state.materialDraft.base_material_id = materialId;
      state.materialDraft.surface_id = defaultSurfaceId;
      state.materialDraft.profile_id = '';
      newMaterialName.value = '';
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
        reflectance_total: parseFloat(customSurfaceReflectance.value || '0') || 0,
        absorption_ratio: parseFloat(customSurfaceAbsorption.value || '0') || 0,
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
          ? 'Drag = local face 박스 선택, Ctrl+Drag = add/remove, Shift+Drag = rotate, Wheel = zoom.'
          : 'Drag = rotate, Wheel = zoom. 선택 모드에서만 Click 선택.';
        componentSelectBlock.classList.add('hidden-block');
        faceIndexBlock.classList.add('hidden-block');
        return;
      }}
      if (mode === 'click') {{
        roiModeHint.textContent = '3D view에서 선택: 지금부터 3D viewer 클릭이 ROI 선택으로 동작합니다.';
        viewerTip.textContent = dragSelect
          ? 'Drag = gap target 박스 선택, Ctrl+Drag = add/remove, Shift+Drag = rotate, Wheel = zoom.'
          : 'Drag = rotate, Wheel = zoom. ROI 모드에서만 Click 선택.';
        componentSelectBlock.classList.add('hidden-block');
        faceIndexBlock.classList.add('hidden-block');
      }} else if (mode === 'panel') {{
        roiModeHint.textContent = 'Component 선택: component 체크 또는 face index 입력으로 ROI를 선택합니다.';
        viewerTip.textContent = dragSelect
          ? 'Drag = gap target 박스 선택, Ctrl+Drag = add/remove, Shift+Drag = rotate, Wheel = zoom.'
          : 'Drag = rotate, Wheel = zoom, Camera preset = 정면/측면 보기 고정.';
        componentSelectBlock.classList.remove('hidden-block');
        faceIndexBlock.classList.remove('hidden-block');
      }} else {{
        roiModeHint.textContent = 'ROI 선택 방식이 아직 정해지지 않았습니다. 현재 3D viewer 클릭은 하이라이트만 동작합니다.';
        viewerTip.textContent = dragSelect
          ? 'Drag = gap target 박스 선택, Ctrl+Drag = add/remove, Shift+Drag = rotate, Wheel = zoom.'
          : 'Drag = rotate, Wheel = zoom, Camera preset = 정면/측면 보기 고정.';
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
      state.selectedFaces = new Set(uniqueSorted(merged));
      updateRoiStats();
      updateViewerMode();
      updateGapSelectionStats();
      drawViewer();
    }}

    function refreshSelectionFromObject() {{
      let faces = [];
      for (const id of state.selectedObjectIds) {{
        const item = state.objectsById.get(id);
        if (item && !item.is_truncated) {{
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
        hiddenFaces: getThreeHiddenTransformFaces(),
        overlays: buildThreeTransformOverlays(),
        axisScalePercent: state.axisScalePercent,
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

    function getThreeHiddenTransformFaces() {{
      if (state.gapTargetMode !== 'component_move_gap') return [];
      return uniqueSorted(Array.from(getCommittedTransformFaceSet()));
    }}

    function buildThreeTransformOverlays() {{
      const overlays = [];
      if (!state.mesh) return overlays;
      if (state.gapTargetMode === 'component_move_gap') {{
        for (const rule of state.transformRules) {{
          if (!rule.enabled || rule.target_type !== 'component' || !transformRuleHasAppliedTransform(rule)) continue;
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
        const rule = activeTransformRule();
        const object = rule ? state.objectsById.get(rule.object_id) : null;
        if (
          state.previewOverlayEnabled
          && rule
          && object
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
        const faceIndices = getActivePreviewFaceIndices();
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
        coordReadout.textContent = 'Origin: (0, 0, 0)';
        return;
      }}
      const bboxMin = scene.bboxMin.map(v => Number(v).toFixed(2)).join(', ');
      const bboxMax = scene.bboxMax.map(v => Number(v).toFixed(2)).join(', ');
      const center = scene.center.map(v => Number(v).toFixed(2)).join(', ');
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
      if (state.gapTargetMode !== 'component_move_gap') return committedFaces;
      for (const rule of state.transformRules) {{
        if (!rule.enabled || rule.target_type !== 'component' || !transformRuleHasAppliedTransform(rule)) continue;
        const object = state.objectsById.get(rule.object_id);
        if (!object || !object.face_indices) continue;
        for (const faceIndex of object.face_indices) {{
          committedFaces.add(faceIndex);
        }}
      }}
      return committedFaces;
    }}

    function drawCommittedTransforms(ctx, scene) {{
      if (!scene || state.gapTargetMode !== 'component_move_gap') return;
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
        if (rule && object && object.face_indices && object.face_indices.length && activeEditorDiffersFromRule()) {{
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
        const faceIndices = getActivePreviewFaceIndices();
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
        const isSelected = state.selectedFaces.has(tri.idx);
        if (mode === 'roi' && !isSelected) continue;
        if (pointInTriangle(px, py, tri.p0, tri.p1, tri.p2)) {{
          return tri.idx;
        }}
      }}
      return null;
    }}

    function updateEmitterPanel() {{
      const t = emitterType.value;
      emitterFace.disabled = t !== 'face';
      emitterNormal.disabled = (t !== 'face' && t !== 'volume_box' && t !== 'volume_sphere');
      emitterBoxMin.disabled = t !== 'volume_box';
      emitterBoxMax.disabled = t !== 'volume_box';
      emitterCenter.disabled = t !== 'volume_sphere';
      emitterRadius.disabled = t !== 'volume_sphere';
    }}

    function currentMoveMagnitude() {{
      return vectorMagnitude(state.gapMove);
    }}

    function currentTiltMagnitude() {{
      return vectorMagnitude(state.gapTilt);
    }}

    function getSurfaceCluster(seedFaceIndex) {{
      if (seedFaceIndex === null || seedFaceIndex === undefined || !state.mesh) return [];
      const visited = new Set();
      const queue = [seedFaceIndex];
      const seedNormal = faceNormal(seedFaceIndex);
      const cluster = [];
      while (queue.length) {{
        const faceIndex = queue.pop();
        if (visited.has(faceIndex)) continue;
        visited.add(faceIndex);
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
          collected.add(member);
        }}
      }}
      setLocalGapFaces(Array.from(collected), additive);
    }}

    function setSelectedGapObjects(objectIds, additive, popupPosition) {{
      const next = additive ? new Set(state.selectedGapObjectIds) : new Set();
      for (const objectId of objectIds) {{
        if (!state.objectsById.has(objectId)) continue;
        if (additive && next.has(objectId)) {{
          next.delete(objectId);
        }} else {{
          next.add(objectId);
        }}
      }}
      const ordered = uniqueSorted(Array.from(next));
      state.selectedGapObjectIds = new Set(ordered);
      state.selectedGapObjectId = ordered.length ? ordered[0] : null;
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
      if (!state.objectsById.has(objectId)) return;
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
      if (!state.objectsById.has(objectId)) return;
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
      const manual = parseFaceList(gapFaceInput.value);
      const merged = uniqueSorted(Array.from(state.localGapFaces).concat(manual));
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
        runBtn.disabled = true;
        importCadBtn.disabled = false;
        useSampleBtn.disabled = false;
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
        if (!res.ok) throw new Error('API error');
        const payload = await res.json();
        state.mesh = payload.mesh;
        state.objectsById.clear();
        state.faceToObjectId.clear();
        buildFaceAdjacency();
        objectList.innerHTML = '';
        gapObjectList.innerHTML = '';
        resetRoiSelection();
        resetGapSelection();

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
            row.innerHTML = '<label><input type=\"checkbox\" data-id=\"' + item.object_id + '\"/> ' + objectLabel(item) + '</label>';
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
              + '<div class=\"name\">' + item.object_name + '</div>'
              + '<div class=\"meta\">faces: ' + item.face_count + ' / area: ' + item.area_mm2 + ' mm2</div>'
              + '</div>'
              + '<div class=\"component-row-actions\">'
              + '<button type=\"button\" class=\"mini-btn\" data-component-transform=\"' + item.object_id + '\">Transform</button>'
              + '<button type=\"button\" class=\"mini-btn ghost\" data-component-material=\"' + item.object_id + '\">Material</button>'
              + '</div>'
              + '</div>';
            const selectArea = gapRow.querySelector('[data-component-select]');
            const transformBtn = gapRow.querySelector('[data-component-transform]');
            const materialBtn = gapRow.querySelector('[data-component-material]');
            selectArea.addEventListener('click', function (ev) {{
              const id = parseInt(ev.currentTarget.getAttribute('data-component-select'), 10);
              setSelectedGapObject(id, null, true);
            }});
            transformBtn.addEventListener('click', function (ev) {{
              const id = parseInt(ev.currentTarget.getAttribute('data-component-transform'), 10);
              startTransformForObject(id, ev);
            }});
            materialBtn.addEventListener('click', function (ev) {{
              const id = parseInt(ev.currentTarget.getAttribute('data-component-material'), 10);
              focusMaterialForObject(id, ev);
            }});
            gapObjectList.appendChild(gapRow);
          }}
        }}

        kpiFaces.textContent = String(payload.metadata.face_count);
        kpiVerts.textContent = String(payload.metadata.vertex_count);
        kpiMode.textContent = payload.metadata.synthetic ? 'Synthetic' : 'CAD';
        if (!cad) {{
          cadFileName.value = 'Sample geometry (no CAD file)';
        }}
        cadMeta.textContent = 'Loaded: ' + (payload.metadata.source_file || 'sample geometry') + ' / ' + payload.metadata.import_note;
        syncComponentSelectionSummary();
        renderMaterialLibrary();
        updateMaterialTargetSummary();
        renderTransformRules();
        updateViewerMode();
        updateGapSelectionStats();
        drawViewer();
      }} catch (err) {{
        setResultMessage('<div><b>Load failed:</b> ' + err.message + '</div>');
      }} finally {{
        runBtn.disabled = true;
        importCadBtn.disabled = false;
        useSampleBtn.disabled = false;
        runBtn.textContent = 'Run simulation (temporarily off)';
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
      for (const tri of scene.triList) {{
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
        if (state.gapTargetMode === 'face_gap') {{
          if (state.gapSelectionMethod === 'drag_box') {{
            setInspectedFace(faceIndex);
            return;
          }}
          setInspectedFace(faceIndex);
          selectLocalGapCluster(faceIndex, !!(ev.ctrlKey || ev.metaKey));
        }} else if (state.roiSelectionMode === 'click') {{
          toggleClickedFace(faceIndex);
        }} else {{
          setInspectedFace(faceIndex);
          if (state.gapTargetMode === 'component_move_gap') {{
            if (state.gapSelectionMethod === 'drag_box') {{
              return;
            }}
            const objectId = faceIndex === null || faceIndex === undefined ? null : state.faceToObjectId.get(faceIndex);
            setSelectedGapObject(objectId, null, !!(ev.ctrlKey || ev.metaKey));
          }}
        }}
      }}

      fullCanvas.addEventListener('click', function (ev) {{
        handlePick(ev, 'full');
      }});
      roiCanvas.addEventListener('click', function (ev) {{
        handlePick(ev, 'roi');
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
      cadFileName.value = 'demo_tv_frame.obj';
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
    window.addEventListener('mousemove', movePopupDrag);
    window.addEventListener('mousemove', moveMaterialPopupDrag);
    window.addEventListener('mouseup', stopPopupDrag);
    window.addEventListener('mouseup', stopMaterialPopupDrag);
    window.addEventListener('mouseleave', stopPopupDrag);
    window.addEventListener('mouseleave', stopMaterialPopupDrag);
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
    axisScale.addEventListener('input', function () {{
      state.axisScalePercent = parseInt(axisScale.value, 10) || 100;
      axisScaleValue.textContent = state.axisScalePercent + '%';
      drawViewer();
    }});
    window.addEventListener('leakage-three-ready', function () {{
      updateViewerEngineUI();
      drawViewer();
    }});
    emitterType.addEventListener('change', updateEmitterPanel);
    runForm.addEventListener('keydown', function (ev) {{
      if (ev.key !== 'Enter') return;
      const target = ev.target;
      if (!target || target.tagName === 'TEXTAREA') return;
      ev.preventDefault();
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
      setResultMessage('<div>Run simulation은 현재 잠시 꺼져 있습니다. 입력한 move / tilt / ROI 값만 반영됩니다.</div>');
      updateGapSelectionStats();
      drawViewer();
    }});

    window.addEventListener('resize', drawViewer);
    updateEmitterPanel();
    setSidebarLayout('vertical');
    ensureMaterialLibraryState();
    renderMaterialLibrary();
    state.gapSelectionMethod = gapSelectionMethod.value;
    updateSelectionModeUI();
    updateGapModeUI();
    syncTransformInputs();
    updateGapSelectionStats();
    updateMaterialTargetSummary();
    runBtn.disabled = true;
    runBtn.textContent = 'Run simulation (temporarily off)';
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


def run_server(host: str = "127.0.0.1", port: int = 8787) -> None:
    start_port = port
    last_error: Optional[OSError] = None
    max_tries = 24

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
    run_server(port=port)


if __name__ == "__main__":
    main()

