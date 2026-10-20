"""Immutable measured-BRDF buffers and strict FP64 CPU/CUDA sampling.

Tables are prepared once on the host; RT lookup, AOI mixture selection, cell
selection and rejection sampling run on the selected compute device. The
independent scalar implementation in bsdf_transport remains a physics oracle.
Semantic RNG lanes 256+ are reserved for BSDF (including rejection attempts).
"""
from dataclasses import dataclass
import math

import numpy as np

from .bsdf_transport import _bracket

PACKED_CONTRACT = "bsdf_packed_no_shift_fp64_v1"


def _readonly(values, columns, dtype=np.float64):
    array = np.array(values, dtype=dtype).reshape((-1, columns))
    array.setflags(write=False)
    return array


@dataclass(frozen=True)
class PackedBsdf:
    # Material: first wavelength record, count (only selected wavelength neighbours).
    materials: np.ndarray
    # Wave: weight, first table, table count, first RT row, RT row count.
    waves: np.ndarray
    # Table: incidence degrees, first cell, cell count.
    tables: np.ndarray
    # Cell: cumulative probability, theta0/1, phi0/1, four BRDF corners, maximum.
    cells: np.ndarray
    # RT: incidence degrees, absolute reflectance.
    rt: np.ndarray

    @classmethod
    def empty(cls):
        return cls(_readonly([], 2, np.int64), _readonly([], 5),
                   _readonly([], 3), _readonly([], 10), _readonly([], 2))

    def arrays(self):
        return self.materials, self.waves, self.tables, self.cells, self.rt

    @property
    def nbytes(self):
        return sum(a.nbytes for a in self.arrays())


def pack_bsdf_profiles(profiles):
    """Deduplicate compiled assets across faces; never consult legacy optics."""
    materials, waves, tables, cell_blocks, rt = [], [], [], [], []
    ids, lookup, cell_count = [], {}, 0
    for profile in profiles:
        model = getattr(profile, "_bsdf_transport", None)
        if model is None:
            if profile.bsdf_asset_id:
                raise ValueError("BSDF profile must be validated and compiled before packing")
            ids.append(-1)
            continue
        key = (model.asset_id, model.wavelength_nm)
        if key not in lookup:
            lookup[key] = len(materials)
            wave_bracket = _bracket(model.wavelengths, model.wavelength_nm)
            materials.append((len(waves), len(wave_bracket)))
            for wi, weight in wave_bracket:
                axis, reflectances = model.rt[wi]
                waves.append((weight, len(tables), len(model.tables[wi]), len(rt), len(axis)))
                rt.extend(zip(axis, reflectances))
                for angle, table in zip(model.angles[wi], model.tables[wi]):
                    rows, cols = table.cell_max.shape
                    block = np.column_stack((
                        table.cell_cdf,
                        np.repeat(table.theta[:-1], cols),
                        np.repeat(table.theta[1:], cols),
                        np.tile(table.phi[:-1], rows),
                        np.tile(table.phi[1:], rows),
                        table.values[:-1, :-1].ravel(), table.values[:-1, 1:].ravel(),
                        table.values[1:, :-1].ravel(), table.values[1:, 1:].ravel(),
                        table.cell_max.ravel(),
                    ))
                    tables.append((angle, cell_count, len(block)))
                    cell_blocks.append(block)
                    cell_count += len(block)
        ids.append(lookup[key])
    packed = PackedBsdf(_readonly(materials, 2, np.int64), _readonly(waves, 5),
                        _readonly(tables, 3),
                        _readonly(np.concatenate(cell_blocks) if cell_blocks else [], 10),
                        _readonly(rt, 2))
    face_ids = np.array(ids, dtype=np.int64)
    face_ids.setflags(write=False)
    return packed, face_ids


def validate_bsdf_indices(packed, indices, scatter, bsdf_code):
    if not isinstance(packed, PackedBsdf):
        raise ValueError("BSDF buffers must be PackedBsdf")
    ids = np.array(indices, dtype=np.int64, copy=True)
    if ids.shape != scatter.shape or np.any(ids < -1) or np.any(ids >= len(packed.materials)):
        raise ValueError("BSDF indices do not align with the material table")
    if np.any((ids >= 0) != (scatter == bsdf_code)):
        raise ValueError("BSDF material indices and scatter codes disagree")
    ids.setflags(write=False)
    return ids


def make_bsdf_functions(jit, uniform):
    """Compile identical arithmetic for Python, Numba CPU or CUDA device code.

    Caller supplies its established semantic counter RNG and compiler. No
    fastmath and no capped-attempt directional fallback are used.
    Incoming direction and incident-side normal must already be unit vectors.
    """
    @jit
    def bracket(data, start, count, angle):
        end = start + count - 1
        if angle <= data[start, 0]:
            return start, start, 0.0
        if angle >= data[end, 0]:
            return end, end, 0.0
        lo, hi = start, end
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if data[mid, 0] < angle:
                lo = mid
            else:
                hi = mid
        t = (angle - data[lo, 0]) / (data[hi, 0] - data[lo, 0])
        return lo, hi, t

    @jit
    def reflectance(material, cosine, materials, waves, tables, cells, rt):
        cosine = 0.0 if cosine < 0.0 else (1.0 if cosine > 1.0 else cosine)
        angle = math.acos(cosine) * (180.0 / math.pi)
        total = 0.0
        start = materials[material, 0]
        count = materials[material, 1]
        for wi in range(start, start + count):
            lo, hi, t = bracket(rt, int(waves[wi, 3]), int(waves[wi, 4]), angle)
            total += waves[wi, 0] * ((1.0 - t) * rt[lo, 1] + t * rt[hi, 1])
        return 0.0 if total < 0.0 else (1.0 if total > 1.0 else total)

    @jit
    def sample(material, dx, dy, dz, nx, ny, nz, key, depth,
               materials, waves, tables, cells, rt):
        cosine = -(dx*nx + dy*ny + dz*nz)
        cosine = 0.0 if cosine < 0.0 else (1.0 if cosine > 1.0 else cosine)
        angle = math.acos(cosine) * (180.0 / math.pi)
        # Independently normalized table distributions are mixed by AOI and
        # wavelength. RT is evaluated separately (never folded into weights).
        draw = uniform(key, depth, 256)
        ws, wc = materials[material, 0], materials[material, 1]
        chosen = int(waves[ws + wc - 1, 1] + waves[ws + wc - 1, 2] - 1)
        selected = False
        for wi in range(ws, ws + wc):
            lo, hi, t = bracket(tables, int(waves[wi, 1]), int(waves[wi, 2]), angle)
            draw -= waves[wi, 0] * (1.0-t)
            if draw < 0.0:
                chosen, selected = lo, True
                break
            draw -= waves[wi, 0] * t
            if draw < 0.0:
                chosen, selected = hi, True
                break
        if not selected:
            # Round-off at the end of the mixture: last bracketing table,
            # not the last measured AOI, which may be outside this mixture.
            chosen = hi
        start = int(tables[chosen, 1])
        count = int(tables[chosen, 2])
        draw = uniform(key, depth, 257)
        lo, hi_cell = start, start + count
        while lo < hi_cell:
            mid = (lo + hi_cell) // 2
            if cells[mid, 0] <= draw:
                lo = mid + 1
            else:
                hi_cell = mid
        cell = lo if lo < start + count else start + count - 1
        a, b = cells[cell, 1], cells[cell, 2]
        p0, p1 = cells[cell, 3], cells[cell, 4]
        sin_a, sin_b = math.sin(a), math.sin(b)
        sa, sb = sin_a*sin_a, sin_b*sin_b
        attempt = 0
        while True:
            lane = 258 + 3*attempt
            sin_squared = sa+(sb-sa)*uniform(key, depth, lane)
            theta = math.asin(math.sqrt(1.0 if sin_squared > 1.0 else sin_squared))
            u, v = (theta-a)/(b-a), uniform(key, depth, lane+1)
            value = ((1.0-u)*((1.0-v)*cells[cell, 5]+v*cells[cell, 6])
                     + u*((1.0-v)*cells[cell, 7]+v*cells[cell, 8]))
            attempt += 1
            if uniform(key, depth, lane+2)*cells[cell, 9] <= value:
                break
        phi = p0 + (p1-p0)*v
        # LT incident projection toward source = +local Y (longitude 90).
        yx, yy, yz = -dx-cosine*nx, -dy-cosine*ny, -dz-cosine*nz
        length2 = yx*yx + yy*yy + yz*yz
        if length2 < 1e-24:
            hx, hy = (1.0, 0.0) if abs(nx) < 0.9 else (0.0, 1.0)
            projection = hx*nx + hy*ny
            yx, yy, yz = hx-projection*nx, hy-projection*ny, -projection*nz
            length2 = yx*yx + yy*yy + yz*yz
        inv = 1.0 / math.sqrt(length2)
        yx, yy, yz = yx*inv, yy*inv, yz*inv
        xx, xy, xz = yy*nz-yz*ny, yz*nx-yx*nz, yx*ny-yy*nx
        st = math.sin(theta)
        x, y, z = st*math.cos(phi), st*math.sin(phi), math.cos(theta)
        return (x*xx+y*yx+z*nx, x*xy+y*yy+z*ny, x*xz+y*yz+z*nz,
                2+3*attempt)

    return reflectance, sample
