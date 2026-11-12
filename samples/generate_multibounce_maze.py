"""Generate a small, two-component STEP model for multi-bounce leak tests.

Coordinates are millimetres. Light enters at the negative-X end and can leave
at the positive-X end only after navigating alternating, opaque baffles.
"""

from __future__ import annotations

from pathlib import Path

import cadquery as cq


OUTPUT = Path(__file__).with_name("multibounce_maze_two_components.stp")


def box(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> cq.Workplane:
    return cq.Workplane("XY").box(x1 - x0, y1 - y0, z1 - z0).translate(
        ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
    )


def make_maze() -> cq.Assembly:
    # Component 1: bottom tray, side/end walls and a baffle that leaves a
    # passage only at high Y. Apertures in the end walls are real holes.
    tray = box(0, 120, 0, 60, 0, 3)
    tray = tray.union(box(0, 120, 0, 3, 3, 21))
    tray = tray.union(box(0, 120, 57, 60, 3, 21))
    tray = tray.union(box(0, 3, 3, 57, 3, 21))
    tray = tray.union(box(117, 120, 3, 57, 3, 21))
    tray = tray.union(box(40, 43, 3, 42, 3, 21))
    tray = tray.cut(box(-1, 4, 24, 36, 8, 16))  # inlet: -X, centred Y=30
    tray = tray.cut(box(116, 121, 6, 18, 8, 16))  # outlet: +X, centred Y=12

    # Component 2: top cover with the opposing baffle. The first baffle's
    # open interval is Y=42..57, this one's is Y=3..18; they do not overlap.
    cover = box(3, 117, 3, 57, 21, 24)
    cover = cover.union(box(77, 80, 18, 57, 3, 21))

    assembly = cq.Assembly(name="MultiBounce_Maze_Two_Components")
    assembly.add(tray, name="Maze_Tray_A", color=cq.Color(0.42, 0.53, 0.65))
    assembly.add(cover, name="Maze_Cover_B", color=cq.Color(0.77, 0.57, 0.34))
    return assembly


def main() -> None:
    assembly = make_maze()
    assembly.save(str(OUTPUT), exportType="STEP", mode="default", write_pcurves=False)
    print(f"STEP: {OUTPUT}")
    print(f"size: {OUTPUT.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
