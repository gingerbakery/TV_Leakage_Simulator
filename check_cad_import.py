from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import traceback
from datetime import datetime
from typing import Optional

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.append(str(ROOT / "src"))

from leakage_simulator.roi import build_scene_payload


def _pick_cad_file() -> Optional[str]:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(
        title="Select CAD file",
        filetypes=[
            ("CAD files", "*.stp *.step *.obj *.stl *.x_t"),
            ("STEP", "*.stp *.step"),
            ("All files", "*.*"),
        ],
    )
    root.destroy()
    return path or None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Direct CAD import checker")
    parser.add_argument("--cad", type=str, default=None, help="Path to CAD file")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="outputs/import_check",
        help="Where to save the import summary json",
    )
    parser.add_argument(
        "--no-dialog",
        action="store_true",
        default=False,
        help="Do not open file picker when --cad is omitted",
    )
    parser.add_argument(
        "--fast-import",
        action="store_true",
        default=False,
        help="Skip display-only ROI mesh subdivision for diagnosis",
    )
    parser.add_argument(
        "--skip-product-metadata",
        action="store_true",
        default=False,
        help="Skip STEP component name/color parsing for diagnosis",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    cad_path = args.cad
    if not cad_path and not args.no_dialog:
        cad_path = _pick_cad_file()

    if not cad_path:
        print("[ERR] No CAD file selected.")
        print("Use: check_cad_import.py --cad C:\\path\\to\\file.stp")
        return 1

    cad_file = pathlib.Path(cad_path)
    print("[INFO] CAD file:", cad_file)
    if args.fast_import:
        os.environ["LEAKAGE_CAD_FAST_IMPORT"] = "1"
        print("[INFO] Fast import diagnostic: ON")
    if args.skip_product_metadata:
        os.environ["LEAKAGE_CAD_SKIP_PRODUCT_METADATA"] = "1"
        print("[INFO] Product metadata diagnostic: SKIPPED")
    print("[INFO] Checking import...")

    try:
        started_at = time.perf_counter()
        payload = build_scene_payload(str(cad_file))
        payload_json = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        elapsed_sec = time.perf_counter() - started_at
    except Exception:
        print("[ERR] Import failed with exception:")
        print(traceback.format_exc())
        return 2

    output_dir = ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_path = output_dir / f"import_check_{stamp}.json"

    metadata = payload.get("metadata", {})
    mesh = payload.get("mesh", {})
    summary = {
        "cad_path": str(cad_file),
        "file_size_mb": round(cad_file.stat().st_size / (1024 * 1024), 4),
        "elapsed_sec": round(elapsed_sec, 4),
        "scene_payload_mb": round(len(payload_json) / (1024 * 1024), 4),
        "synthetic": bool(metadata.get("synthetic")),
        "import_note": metadata.get("import_note", ""),
        "import_timings_sec": metadata.get("import_timings_sec", {}),
        "face_count": int(metadata.get("face_count", len(mesh.get("faces", [])))),
        "vertex_count": int(
            metadata.get("vertex_count", len(mesh.get("vertices", [])))
        ),
        "receiver_face_hint_count": len(
            metadata.get("receiver_face_hint", [])
        ),
        "object_count": len(payload.get("objects", [])),
        "source_file": metadata.get("source_file", ""),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[INFO] Import finished.")
    print("[INFO] synthetic =", summary["synthetic"])
    print("[INFO] note      =", summary["import_note"])
    print("[INFO] faces     =", summary["face_count"])
    print("[INFO] vertices  =", summary["vertex_count"])
    print("[INFO] objects   =", summary["object_count"])
    print("[INFO] payload   =", summary["scene_payload_mb"], "MB")
    print("[INFO] elapsed   =", summary["elapsed_sec"], "sec")
    print("[INFO] summary   =", summary_path)

    if summary["synthetic"]:
        print("[WARN] Real CAD import did not complete; synthetic fallback was used.")
        return 3

    print("[OK] Real CAD import succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
