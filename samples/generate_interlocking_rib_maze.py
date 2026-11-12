"""Generate a 30 x 30 mm, three-component STEP light-path test fixture.

The cover is deliberately also the third optical component: its tongue rib
enters the U-shaped fork of the separate insert without a volume collision.
All coordinates are millimetres. The cavity is closed except for apertures
in the -X and +X end walls.
"""

from __future__ import annotations

from pathlib import Path

import cadquery as cq


OUTPUT = Path(__file__).with_name("interlocking_rib_maze_30x30.stp")


def box(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> cq.Workplane:
    return cq.Workplane("XY").box(x1 - x0, y1 - y0, z1 - z0).translate(
        ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
    )


def build_parts() -> tuple[cq.Workplane, cq.Workplane, cq.Workplane]:
    # A: shallow outer tray. Both end openings are real through-holes.
    tray = box(0, 30, 0, 30, 0, 1.5)
    tray = tray.union(box(0, 30, 0, 1.5, 1.5, 10.5))
    tray = tray.union(box(0, 30, 28.5, 30, 1.5, 10.5))
    tray = tray.union(box(0, 1.5, 1.5, 28.5, 1.5, 10.5))
    tray = tray.union(box(28.5, 30, 1.5, 28.5, 1.5, 10.5))
    tray = tray.cut(box(-1, 2, 12, 18, 4, 8))
    tray = tray.cut(box(28, 31, 12, 18, 4, 8))

    # Short reinforcing ribs evoke a moulded housing without pretending that
    # they are the main optical walls. They are deliberately clear of B/C.
    for x0, x1, y0, y1 in (
        (2.5, 5.5, 4.0, 4.45),
        (2.5, 5.5, 25.55, 26.0),
        (26.5, 28.0, 4.0, 4.45),
        (26.5, 28.0, 25.55, 26.0),
    ):
        tray = tray.union(box(x0, x1, y0, y1, 1.5, 2.2))

    # B: one physically connected fork insert: two parallel ribs and a
    # closed left end, joined by a low foot. The fork opens toward +X.
    fork = box(6.5, 22.0, 9.5, 20.5, 1.5, 2.1)
    fork = fork.union(box(6.5, 22.0, 9.5, 10.7, 2.1, 10.5))
    fork = fork.union(box(6.5, 22.0, 19.3, 20.5, 2.1, 10.5))
    fork = fork.union(box(6.5, 7.7, 10.7, 19.3, 2.1, 10.5))

    # C: an actual roof, not a transparent viewing-only sheet. Its central
    # tongue enters B's fork: side clearances 3.5 mm, tip clearance 5.3 mm.
    # Two short return ribs near the outlet add a further alternating turn.
    cover = box(1.5, 28.5, 1.5, 28.5, 10.5, 12.0)
    cover = cover.union(box(13.0, 26.0, 14.2, 15.8, 2.1, 10.5))
    cover = cover.union(box(24.0, 25.2, 1.5, 9.5, 1.5, 10.5))
    cover = cover.union(box(24.0, 25.2, 20.5, 28.5, 1.5, 10.5))
    return tray, fork, cover


def make_assembly() -> cq.Assembly:
    tray, fork, cover = build_parts()
    assembly = cq.Assembly(name="Interlocking_Rib_Maze_30x30")
    assembly.add(tray, name="Rib_Tray_A", color=cq.Color(0.30, 0.37, 0.44))
    assembly.add(fork, name="Fork_Insert_B", color=cq.Color(0.38, 0.53, 0.68))
    assembly.add(cover, name="Tongue_Cover_C", color=cq.Color(0.73, 0.74, 0.69))
    return assembly


def main() -> None:
    assembly = make_assembly()
    assembly.save(str(OUTPUT), exportType="STEP", mode="default", write_pcurves=False)
    print(f"STEP: {OUTPUT}")
    print(f"size: {OUTPUT.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
