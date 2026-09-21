from __future__ import annotations

from dataclasses import replace
import math

from .types import RayTraceConfig


def emitter_termination_config(config: RayTraceConfig, nominal_ray_lumen: float) -> RayTraceConfig:
    if config.min_energy_basis == "absolute_lumen":
        return config
    return replace(config, min_energy=config.min_energy * nominal_ray_lumen, min_energy_basis="absolute_lumen")


def termination_summary(
    config: RayTraceConfig,
    optical: dict,
    reflection: dict,
    thresholds: list[float],
    source_power: float,
    stopped_early: bool,
    weighted_sampling: bool,
) -> dict:
    threshold_mode = config.termination_mode == "threshold"
    unweighted_transport = threshold_mode and not weighted_sampling
    potential = math.fsum(
        entry["potential_reflected_flux_lumen"] for entry in optical["profile_hits"].values()
    )
    emitted = math.fsum(entry["emitted_flux_lumen"] for entry in reflection["lobes"].values())
    maximum_threshold = max(thresholds, default=0.0)
    stop_count = (
        reflection["reflection_below_energy_count"]
        + reflection["reflection_disabled_count"]
        + reflection["depth_limit_count"]
    )
    unpropagated = max(0.0, potential - emitted) if stop_count else 0.0
    return {
        "contract": "nominal_packet_termination_v1",
        "min_energy_basis": config.min_energy_basis,
        "min_energy": config.min_energy,
        "termination_mode": config.termination_mode,
        "source_power_lumen": source_power,
        "energy_cutoff_upper_bound_lumen": (
            reflection["reflection_below_energy_count"] * maximum_threshold if threshold_mode else None
        ),
        "unpropagated_surface_flux_lumen": unpropagated if unweighted_transport else None,
        "loss_accounting_scope": "threshold_source_only" if unweighted_transport else "weighted_transport_unavailable",
        "depth_limit_count": reflection["depth_limit_count"],
        "energy_cutoff_count": reflection["reflection_below_energy_count"] if threshold_mode else 0,
        "roulette_terminated_count": reflection["roulette_terminated_count"],
        "stopped_early": stopped_early,
    }
