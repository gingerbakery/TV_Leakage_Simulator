from __future__ import annotations

from collections.abc import Mapping
import copy

from .bsdf_transport import compile_asset


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


def validate_bsdf_profiles(profiles, assets=(), config=None):
    references = set()
    for profile in profiles:
        asset_id = _get(profile, "bsdf_asset_id")
        if asset_id is None or asset_id == "":
            continue
        if not isinstance(asset_id, str) or not asset_id.strip():
            raise ValueError("Invalid BSDF asset ID; analytic fallback is not allowed")
        references.add(asset_id)
    if not references:
        return {}
    for key in ("primary_sampling_strategy", "bounce_sampling_strategy"):
        if _get(config, key, "source") != "source":
            raise ValueError("BSDF 표 계산은 현재 source sampling만 지원합니다 (receiver MIS 미지원).")
    if not isinstance(assets, (list, tuple)) or len(assets) > 64:
        raise ValueError("BSDF assets must be a list with at most 64 entries")
    by_id = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("id"), str):
            raise ValueError("Invalid BSDF asset")
        if asset["id"] in by_id:
            raise ValueError("Duplicate BSDF asset ID")
        by_id[asset["id"]] = asset
    models = {}
    for asset_id in references:
        if not isinstance(asset_id, str) or asset_id not in by_id:
            raise ValueError(f"BSDF asset {asset_id!r} is missing; analytic fallback is not allowed")
        models[asset_id] = compile_asset(by_id[asset_id])
    return models


def bind_bsdf_profiles(profiles, assets, config):
    models = validate_bsdf_profiles(profiles, assets, config)
    bound = []
    for profile in profiles:
        local = copy.copy(profile)
        # Runtime data stays outside serialized dataclass fields.
        local._bsdf_transport = models.get(local.bsdf_asset_id)
        bound.append(local)
    return bound, models


def validate_bsdf_request(payload):
    return validate_bsdf_profiles(payload.get("optical_profiles", []), payload.get("bsdf_assets", []), payload.get("config") or {})
