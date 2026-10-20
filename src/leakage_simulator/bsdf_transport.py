"""Measured, isotropic reflection in air; explicit No Shift transport.

The LT ASCII table contains relative BRDF, not direction probability. We
integrate its bilinear (polar angle, azimuth) interpolant against cos(theta)
dOmega, then sample that same interpolant. Absolute reflected power comes
only from RT. See docs/bsdf-transport-validation.md for the supported contract.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from functools import lru_cache
import json
import math
import random
from typing import Any

import numpy as np

from .bsdf_import import import_lt_ascii_bsdf

CONTRACT = "bsdf_isotropic_no_shift_v1"


def _unit(v):
    length = math.sqrt(sum(x*x for x in v))
    if length <= 1e-15:
        raise ValueError("BSDF direction/normal must be nonzero")
    return tuple(x/length for x in v)


def incident_frame(incoming, normal):
    d, n = _unit(incoming), _unit(normal)
    dot = sum(a*b for a, b in zip(d, n))
    if dot > 0:
        n = tuple(-x for x in n)
        dot = -dot
    cosine = min(1., max(0., -dot))
    # LT: direction TOWARDS source is longitude 90; mirror exit is 270.
    y = tuple(-d[k] - cosine*n[k] for k in range(3))
    if sum(x*x for x in y) < 1e-24:
        helper = (1., 0., 0.) if abs(n[0]) < .9 else (0., 1., 0.)
        projection = sum(a*b for a, b in zip(helper, n))
        y = tuple(helper[k] - projection*n[k] for k in range(3))
    y = _unit(y)
    x = (y[1]*n[2]-y[2]*n[1], y[2]*n[0]-y[0]*n[2], y[0]*n[1]-y[1]*n[0])
    return math.degrees(math.acos(cosine)), x, y, n


def _bracket(axis, value):
    """Endpoint clamp is explicit in the persisted transport configuration."""
    if value <= axis[0]:
        return ((0, 1.),)
    if value >= axis[-1]:
        return ((len(axis)-1, 1.),)
    upper = bisect_left(axis, value)
    if axis[upper] == value:
        return ((upper, 1.),)
    t = (value-axis[upper-1])/(axis[upper]-axis[upper-1])
    return ((upper-1, 1.-t), (upper, t))


@dataclass(frozen=True)
class PolarTable:
    theta: np.ndarray
    phi: np.ndarray
    values: np.ndarray
    cell_cdf: np.ndarray
    cell_max: np.ndarray
    normalization: float

    @classmethod
    def build(cls, grid):
        theta = np.radians(np.array(grid["latitudesDeg"][::-1], dtype=np.float64))
        phi = np.radians(np.array(grid["longitudesDeg"], dtype=np.float64))
        if not (abs(theta[0]) < 1e-12 and abs(theta[-1]-math.pi/2) < 1e-12
                and abs(phi[0]) < 1e-12 and abs(phi[-1]-2*math.pi) < 1e-12):
            raise ValueError("BSDF tracing requires a complete 0–90° × 0–360° hemisphere; partial tables are import-only")
        values = np.array(grid["values"][::-1], dtype=np.float64)
        # Scaling relative data is immaterial; prevents overflow in integration.
        values /= values.max()
        a, b = theta[:-1], theta[1:]
        # Gaussian quadrature integrates the smooth angular factor accurately,
        # even on narrow cells where subtracting analytic primitives cancels.
        nodes, weights = np.polynomial.legendre.leggauss(16)
        t = (nodes+1)/2
        angles = a[:, None] + (b-a)[:, None]*t
        measure = np.sin(angles)*np.cos(angles)*(b-a)[:, None]*weights/2
        w0, w1 = measure @ (1-t), measure @ t
        masses = np.diff(phi)[None, :] * (
            w0[:, None]*(values[:-1, :-1]+values[:-1, 1:])/2
            + w1[:, None]*(values[1:, :-1]+values[1:, 1:])/2)
        normalization = float(masses.sum())
        if not math.isfinite(normalization) or normalization <= 0:
            raise ValueError("BSDF table has no finite projected scattering energy")
        cdf = np.cumsum(masses.ravel())
        cdf /= cdf[-1]
        cdf[-1] = 1.
        maxima = np.maximum.reduce([values[:-1, :-1], values[:-1, 1:], values[1:, :-1], values[1:, 1:]])
        for array in (theta, phi, values, cdf, maxima):
            array.setflags(write=False)
        return cls(theta, phi, values, cdf, maxima, normalization)

    def sample(self, rng):
        index = min(int(np.searchsorted(self.cell_cdf, rng.random(), side="right")), len(self.cell_cdf)-1)
        row, col = divmod(index, len(self.phi)-1)
        a, b = self.theta[row:row+2]
        p0, p1 = self.phi[col:col+2]
        f00, f01 = self.values[row, col:col+2]
        f10, f11 = self.values[row+1, col:col+2]
        maximum = self.cell_max[row, col]
        sa, sb = math.sin(a)**2, math.sin(b)**2
        # Proposal is cos(theta) sin(theta) dtheta dphi within this cell;
        # acceptance is the bilinear BRDF. No capped-retry specular fallback.
        while True:
            theta = math.asin(math.sqrt(sa + (sb-sa)*rng.random()))
            u, v = (theta-a)/(b-a), rng.random()
            value = (1-u)*((1-v)*f00+v*f01)+u*((1-v)*f10+v*f11)
            if rng.random()*maximum <= value:
                return theta, p0+(p1-p0)*v

    def relative_value(self, theta, phi):
        """Bilinear BRDF for diagnostics; theta/phi are radians."""
        if not 0 <= theta <= math.pi/2:
            return 0.
        phi %= 2*math.pi
        row = min(max(int(np.searchsorted(self.theta, theta))-1, 0), len(self.theta)-2)
        col = min(max(int(np.searchsorted(self.phi, phi))-1, 0), len(self.phi)-2)
        u = (theta-self.theta[row])/(self.theta[row+1]-self.theta[row])
        v = (phi-self.phi[col])/(self.phi[col+1]-self.phi[col])
        z = self.values
        return float((1-u)*((1-v)*z[row,col]+v*z[row,col+1])+u*((1-v)*z[row+1,col]+v*z[row+1,col+1]))


@dataclass(frozen=True)
class BsdfTransport:
    asset_id: str
    wavelength_nm: float
    wavelengths: tuple
    angles: tuple
    tables: tuple
    rt: tuple

    def reflectance_at(self, incidence_deg):
        result = 0.
        for wi, weight in _bracket(self.wavelengths, self.wavelength_nm):
            axis, values = self.rt[wi]
            result += weight*sum(w*values[i] for i, w in _bracket(axis, incidence_deg))
        return result

    def reflectance(self, incoming, normal):
        return self.reflectance_at(incident_frame(incoming, normal)[0])

    def mixture(self, incidence_deg):
        return [(self.tables[wi][ai], ww*aw)
                for wi, ww in _bracket(self.wavelengths, self.wavelength_nm)
                for ai, aw in _bracket(self.angles[wi], incidence_deg)]

    def sample_direction(self, rng, incoming, normal):
        angle, x, y, n = incident_frame(incoming, normal)
        mixture = self.mixture(angle)
        chosen = mixture[-1][0]
        if len(mixture) > 1:
            draw = rng.random()
            for table, weight in mixture:
                draw -= weight
                if draw <= 0:
                    chosen = table
                    break
        theta, phi = chosen.sample(rng)
        xx, yy, zz = math.sin(theta)*math.cos(phi), math.sin(theta)*math.sin(phi), math.cos(theta)
        return tuple(xx*x[k]+yy*y[k]+zz*n[k] for k in range(3))

    def metadata(self):
        return {"asset_id": self.asset_id, "contract": CONTRACT, "wavelength_nm": self.wavelength_nm,
                "symmetry": "isotropic", "shift": "no_shift", "aoi_extrapolation": "clamp",
                "side_mapping": "same_table_on_incident_side", "interpolation": "bilinear_polar_normalized_mixture",
                "execution": "cpu_reference", "lt_validated": False}


def compile_asset(asset: dict[str, Any]) -> BsdfTransport:
    if not isinstance(asset, dict):
        raise ValueError("BSDF asset must be an object")
    options = asset.get("transport")
    if not isinstance(options, dict) or set(options) != {"model", "wavelengthNm", "aoiExtrapolation"}:
        raise ValueError("BSDF 계산 옵션(등방성 / No Shift / 파장)을 먼저 설정하세요.")
    if options["model"] != "isotropic_no_shift" or options["aoiExtrapolation"] != "clamp":
        raise ValueError("Supported BSDF transport: isotropic_no_shift with explicit AOI endpoint clamp")
    wavelength = options["wavelengthNm"]
    if isinstance(wavelength, bool) or not isinstance(wavelength, (float, int)) or not math.isfinite(wavelength):
        raise ValueError("BSDF wavelength must be finite nm")
    # Always reparse embedded originals. Neither readiness nor parsed JSON is trusted.
    parsed = import_lt_ascii_bsdf({"files": asset.get("files")})
    if parsed["id"] != asset.get("id"):
        raise ValueError("BSDF source hash does not match asset ID")
    return _compile(parsed["id"], json.dumps(parsed["data"], sort_keys=True, separators=(",", ":")), float(wavelength))


@lru_cache(maxsize=8)
def _compile(asset_id, data_json, wavelength):
    data = json.loads(data_json)
    wavelengths = tuple(data["wavelengthsNm"])
    if not wavelengths[0] <= wavelength <= wavelengths[-1]:
        raise ValueError("BSDF selected wavelength is outside measured range")
    tables, angles, rt = [], [], []
    for wave in wavelengths:
        grids = sorted((g for g in data["grids"] if g["wavelengthNm"] == wave), key=lambda g:g["incidenceAngleDeg"])
        angles.append(tuple(g["incidenceAngleDeg"] for g in grids))
        tables.append(tuple(PolarTable.build(g) for g in grids))
        rows = [r for r in data["rt"] if r["wavelengthNm"] == wave]
        rt.append((tuple(r["incidenceAngleDeg"] for r in rows), tuple(r["reflectance"] for r in rows)))
    return BsdfTransport(asset_id, wavelength, wavelengths, tuple(angles), tuple(tables), tuple(rt))
