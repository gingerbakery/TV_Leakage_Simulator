"""Read-only observations of actual forward segments, never injected receiver hits.

Each segment is classified once per receiver. Plane projections outside its area
are not arrivals. Geometry/other receivers stop a segment before angular/ROI
gates are counted, so an occluded projection cannot masquerade as an arrival.
"""
import copy
import math

import numpy as np


CATEGORIES = (
    "parallel", "behind_origin", "outside_area", "occluded",
    "other_receiver_first", "back_face", "outside_acceptance", "outside_roi", "accepted",
)


class ReceiverDiagnostics:
    def __init__(self, frames):
        self.escaped_segments = 0
        self.escaped_segment_flux_lumen = 0.0
        self.rows = {f.receiver.receiver_id: dict(
            receiver_id=f.receiver.receiver_id, segments_tested=0,
            receiver_settings=f.receiver.to_dict(),
            counts={key: 0 for key in CATEGORIES},
            segment_flux_lumen={key: 0.0 for key in CATEGORIES},
        ) for f in frames}

    def observe(self, origins, directions, powers, frames, config,
                surface_distances, selected_indices, selected_distances):
        origins = np.asarray(origins, dtype=float)
        directions = np.asarray(directions, dtype=float)
        powers = np.asarray(powers, dtype=float)
        escaped = ~np.isfinite(surface_distances) & (np.asarray(selected_indices) < 0)
        self.escaped_segments += int(np.count_nonzero(escaped))
        self.escaped_segment_flux_lumen += float(np.sum(powers[escaped]))
        for index, frame in enumerate(frames):
            row = self.rows[frame.receiver.receiver_id]
            row["segments_tested"] += len(origins)
            pending = np.ones(len(origins), dtype=bool)

            def take(key, mask):
                selected = pending & mask
                row["counts"][key] += int(np.count_nonzero(selected))
                row["segment_flux_lumen"][key] += float(np.sum(powers[selected]))
                pending[selected] = False

            normal = np.asarray(frame.normal)
            denom = directions @ normal
            take("parallel", np.abs(denom) < 1e-12)
            t = np.divide((np.asarray(frame.receiver.center)-origins) @ normal, denom,
                          out=np.zeros(len(origins)), where=np.abs(denom) >= 1e-12)
            take("behind_origin", t <= config.epsilon_mm)
            points = origins + t[:, None]*directions
            local = points - np.asarray(frame.receiver.center)
            u, v = local @ np.asarray(frame.u_axis), local @ np.asarray(frame.v_axis)
            take("outside_area", (u < -frame.half_width) | (u > frame.half_width)
                 | (v < -frame.half_height) | (v > frame.half_height))
            take("occluded", np.asarray(surface_distances) <= t)
            # Equal-distance receivers follow the production first-in-list rule.
            take("other_receiver_first", (np.asarray(selected_indices) >= 0)
                 & (np.asarray(selected_indices) != index) & (np.asarray(selected_distances) <= t))
            cosine = np.maximum(0., -denom)
            rejected_angle = cosine < frame.minimum_acceptance_cosine
            take("back_face", rejected_angle & (denom > 0))
            take("outside_acceptance", rejected_angle)
            if frame.roi_boxes:
                from .roi_scope import receiver_roi_mask
                take("outside_roi", ~receiver_roi_mask(points, frame.normal, frame.roi_boxes))
            take("accepted", pending.copy())

    def summary(self, grids, total_rays, stopped, termination):
        rows = copy.deepcopy(list(self.rows.values()))
        for row in rows:
            grid = grids[row["receiver_id"]]
            row["actual_receiver_hits"] = grid.hit_count
            row["actual_receiver_flux_lumen"] = sum(sum(line) for line in grid.flux_lumen)
            row["consistent"] = (row["counts"]["accepted"] == grid.hit_count and math.isclose(
                row["segment_flux_lumen"]["accepted"], row["actual_receiver_flux_lumen"],
                rel_tol=1e-9, abs_tol=1e-15))
            row["arrived_count"] = sum(row["counts"][key] for key in
                                       ("back_face", "outside_acceptance", "outside_roi", "accepted"))
        return dict(contract="forward_receiver_segments_v1", complete=not stopped,
                    primary_ray_count=total_rays, receivers=rows,
                    termination=copy.deepcopy(termination),
                    scope="actual_segments_not_unique_rays_or_aperture_flux",
                    execution="host_diagnostics_selected_intersection_backend",
                    escaped_segments=self.escaped_segments,
                    escaped_segment_flux_lumen=self.escaped_segment_flux_lumen,
                    injected_hits=0)
