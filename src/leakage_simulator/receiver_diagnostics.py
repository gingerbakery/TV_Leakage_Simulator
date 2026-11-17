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
        self.emitter_id = None
        self.path_limit = 16
        self.escaped_segments = 0
        self.escaped_segment_flux_lumen = 0.0
        self.rows = {f.receiver.receiver_id: dict(
            receiver_id=f.receiver.receiver_id, segments_tested=0,
            receiver_settings=f.receiver.to_dict(),
            detected_paths=dict(contract="bounded_detected_paths_v1", limit=self.path_limit, samples=[],
                scope="actual_detected_rays_not_reference_replay", truncated=False),
            counts={key: 0 for key in CATEGORIES},
            segment_flux_lumen={key: 0.0 for key in CATEGORIES},
        ) for f in frames}
        for frame in frames:
            path = frame.receiver.observer_reference_path
            if path and len(path) >= 2:
                self.rows[frame.receiver.receiver_id]["reference_sampling"] = dict(
                    contract="reference_origin_neighborhood_v1", point=list(path[-2]), radius_mm=1.0,
                    segments_started=0, min_origin_distance_mm=None,
                    counts={key: 0 for key in CATEGORIES}, examples={})

    def capture_tape(self, tape, frames, emitter_seed, primary_start_index, rng_contract):
        """Read actual detected paths from an already executed bounded event tape."""
        from .wavefront_event_tape import PATH_PAYLOAD_FULL, TERMINAL_RECEIVER
        if tape.path_payload != PATH_PAYLOAD_FULL:
            return
        for slot in np.flatnonzero(tape.terminal_kind_codes == TERMINAL_RECEIVER):
            frame = frames[int(tape.terminal_receiver_indices[slot])]
            row = self.rows[frame.receiver.receiver_id]
            capture = row['detected_paths']
            if len(capture['samples']) >= self.path_limit:
                continue
            start, end = tape.primary_event_bounds(int(slot))
            points = [tape.initial_origins[slot].tolist(), *tape.points[start:end].tolist(), tape.terminal_points[slot].tolist()]
            reference = row.get('reference_sampling')
            nearest = None
            nearest_vertex = None
            if reference:
                point = np.asarray(reference['point'])
                vertices = np.asarray(points)
                delta = vertices[1:] - vertices[:-1]
                denom = np.sum(delta*delta, axis=1)
                fraction = np.clip(np.divide(np.sum((point-vertices[:-1])*delta, axis=1), denom,
                    out=np.zeros(len(delta)), where=denom > 0), 0., 1.)
                nearest = float(np.min(np.linalg.norm(vertices[:-1]+fraction[:, None]*delta-point, axis=1)))
                nearest_vertex = float(np.min(np.linalg.norm(vertices[1:-1]-point, axis=1))) if end > start else None
            capture['samples'].append(dict(
                ray_id=f'{self.emitter_id}:{emitter_seed}:{primary_start_index+int(slot)}',
                emitter_id=self.emitter_id, emitter_seed=int(emitter_seed), primary_index=primary_start_index+int(slot),
                reflection_seed=str(int(tape.reflection_seeds[slot])), rng_contract=rng_contract,
                launch_origin=points[0], launch_direction=tape.initial_directions[slot].tolist(),
                source_face=int(tape.initial_source_faces[slot]), initial_packet_lumen=float(tape.initial_power_lumen[slot]),
                points=points, reflection_count=end-start, flux_lumen=float(tape.terminal_received_power_lumen[slot]),
                surface_faces=tape.face_indices[start:end].tolist(),
                incoming_packet_lumen=tape.incoming_power_lumen[start:end].tolist(),
                outgoing_packet_lumen=tape.emitted_power_lumen[start:end].tolist(),
                surface_status_flags=tape.status_flags[start:end].tolist(),
                selected_point_min_segment_distance_mm=nearest,
                selected_point_min_reflection_distance_mm=nearest_vertex,
                selected_point_radius_mm=reference['radius_mm'] if reference else None))

    def observe(self, origins, directions, powers, frames, config,
                surface_distances, selected_indices, selected_distances,
                surface_faces=None, mesh=None):
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
            reference = row.get("reference_sampling")
            if reference is not None:
                distances = np.linalg.norm(origins - np.asarray(reference["point"]), axis=1)
                nearest = float(np.min(distances)) if len(distances) else None
                if nearest is not None:
                    previous = reference["min_origin_distance_mm"]
                    reference["min_origin_distance_mm"] = nearest if previous is None else min(previous, nearest)
                nearby = distances <= reference["radius_mm"]
                reference["segments_started"] += int(np.count_nonzero(nearby))

            def take(key, mask):
                selected = pending & mask
                row["counts"][key] += int(np.count_nonzero(selected))
                row["segment_flux_lumen"][key] += float(np.sum(powers[selected]))
                pending[selected] = False
                if reference is None:
                    return
                local_indices = np.flatnonzero(selected & nearby)
                reference["counts"][key] += len(local_indices)
                if not len(local_indices):
                    return
                sample_index = int(local_indices[np.argmin(distances[local_indices])])
                previous = reference["examples"].get(key)
                if previous and previous["origin_distance_mm"] <= distances[sample_index]:
                    return
                face = int(surface_faces[sample_index]) if surface_faces is not None else -1
                metadata = mesh.metadata(face) if mesh is not None and face >= 0 else {}
                obstruction = None
                if key == "occluded" and math.isfinite(float(surface_distances[sample_index])):
                    obstruction = (origins[sample_index] + surface_distances[sample_index] * directions[sample_index]).tolist()
                reference["examples"][key] = dict(
                    origin=origins[sample_index].tolist(), direction=directions[sample_index].tolist(),
                    origin_distance_mm=float(distances[sample_index]),
                    obstruction_point=obstruction, obstruction_face_index=face if obstruction is not None else None,
                    obstruction_component_id=metadata.get("step_component_id", metadata.get("component_id")) if obstruction is not None else None,
                    obstruction_component_name=(metadata.get("step_component_name") or metadata.get("object_name")
                        or metadata.get("preview_blocker_id")) if obstruction is not None else None)

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
            capture = row['detected_paths']
            capture['recorded_count'] = len(capture['samples'])
            capture['actual_hit_count'] = grid.hit_count
            capture['truncated'] = grid.hit_count > len(capture['samples'])
            capture['complete'] = not stopped and not capture['truncated']
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
