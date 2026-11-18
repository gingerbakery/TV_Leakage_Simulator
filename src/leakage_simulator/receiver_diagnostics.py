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
                scope="actual_detected_rays_not_reference_replay", truncated=False, tape_hit_count=0),
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

    def capture_tape(self, tape, frames, emitter_seed, primary_start_index, rng_contract,
                     config, profiles, geometry, fraction, bsdf, bsdf_face_ids, sampling_records):
        """Read actual detected paths from an already executed bounded event tape."""
        from .wavefront_event_tape import PATH_PAYLOAD_FULL, TERMINAL_RECEIVER
        if tape.path_payload != PATH_PAYLOAD_FULL:
            return
        for slot in np.flatnonzero(tape.terminal_kind_codes == TERMINAL_RECEIVER):
            frame = frames[int(tape.terminal_receiver_indices[slot])]
            row = self.rows[frame.receiver.receiver_id]
            capture = row['detected_paths']
            capture["tape_hit_count"] += 1
            flux = float(tape.terminal_received_power_lumen[slot])
            samples = capture['samples']
            if len(samples) >= self.path_limit and flux <= samples[-1]['flux_lumen']:
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
                segment_fraction = np.clip(np.divide(np.sum((point-vertices[:-1])*delta, axis=1), denom,
                    out=np.zeros(len(delta)), where=denom > 0), 0., 1.)
                nearest = float(np.min(np.linalg.norm(vertices[:-1]+segment_fraction[:, None]*delta-point, axis=1)))
                nearest_vertex = float(np.min(np.linalg.norm(vertices[1:-1]-point, axis=1))) if end > start else None
            capture['samples'].append(dict(
                ray_id=f'{self.emitter_id}:{emitter_seed}:{primary_start_index+int(slot)}',
                emitter_id=self.emitter_id, emitter_seed=int(emitter_seed), primary_index=primary_start_index+int(slot),
                reflection_seed=str(int(tape.reflection_seeds[slot])), rng_contract=rng_contract,
                launch_origin=points[0], launch_direction=tape.initial_directions[slot].tolist(),
                source_face=int(tape.initial_source_faces[slot]), initial_packet_lumen=float(tape.initial_power_lumen[slot]),
                points=points, reflection_count=end-start, flux_lumen=flux,
                receiver_incoming_packet_lumen=float(tape.terminal_incoming_power_lumen[slot]),
                reflections=_reflection_evidence(tape, int(slot), start, end, points, config,
                    profiles, geometry, fraction, bsdf, bsdf_face_ids, sampling_records),
                surface_faces=tape.face_indices[start:end].tolist(),
                incoming_packet_lumen=tape.incoming_power_lumen[start:end].tolist(),
                outgoing_packet_lumen=tape.emitted_power_lumen[start:end].tolist(),
                surface_status_flags=tape.status_flags[start:end].tolist(),
                selected_point_min_segment_distance_mm=nearest,
                selected_point_min_reflection_distance_mm=nearest_vertex,
                selected_point_radius_mm=reference['radius_mm'] if reference else None))
            samples.sort(key=lambda sample: sample['flux_lumen'], reverse=True)
            del samples[self.path_limit:]

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
            capture['max_single_ray_flux_lumen'] = None
            if not stopped and capture['tape_hit_count'] == grid.hit_count:
                capture['max_single_ray_flux_lumen'] = capture['samples'][0]['flux_lumen'] if grid.hit_count else 0.0
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


def _reflection_evidence(tape, slot, start, end, points, config, profiles,
                         geometry, fraction, bsdf, bsdf_face_ids, sampling_records):
    from types import SimpleNamespace
    from .native_cpu_counter_wavefront import _bsdf_reference_pdf, _receiver_direction_pdf_reference
    from .wavefront_event_tape import STATUS_ROULETTE_SURVIVED

    proposal = None
    if fraction > 0:
        proposal = SimpleNamespace(receiver_centers=geometry.centers, receiver_normals=geometry.normals,
            receiver_u_axes=geometry.u_axes, receiver_v_axes=geometry.v_axes,
            receiver_half_widths=geometry.half_widths, receiver_half_heights=geometry.half_heights,
            epsilon_mm=config.epsilon_mm)
    vertices = np.asarray(points)
    incoming = tape.initial_directions[slot]
    rows = []
    for local, event in enumerate(range(start, end)):
        face = int(tape.face_indices[event])
        profile = profiles[face].profile
        normal = tape.normals[event]
        if np.dot(incoming, normal) > 0:
            normal = -normal
        outgoing = vertices[local+2] - vertices[local+1]
        length = np.linalg.norm(outgoing)
        outgoing = outgoing / length if length > 0 else None
        base_pdf = receiver_pdf = mixture_pdf = None
        applied_fraction = 0.0
        material = int(bsdf_face_ids[face]) if bsdf_face_ids is not None else -1
        if outgoing is not None and material >= 0 and bsdf is not None:
            base_pdf = float(_bsdf_reference_pdf(material, *incoming, *normal, *outgoing, *bsdf.arrays()))
        elif outgoing is not None and profile.scatter_model == "lambertian" and not profile.bsdf_asset_id:
            base_pdf = max(0.0, float(np.dot(normal, outgoing))) / math.pi
        if base_pdf is not None:
            applied_fraction = fraction
            if proposal is not None:
                receiver_pdf = _receiver_direction_pdf_reference(proposal, tuple(vertices[local+1]), tuple(outgoing))
            mixture_pdf = (1-fraction)*base_pdf + fraction*(receiver_pdf or 0.0)
        before = float(tape.incoming_power_lumen[event])
        reflected = float(tape.reflected_power_lumen[event])
        after = float(tape.emitted_power_lumen[event])
        survived = bool(tape.status_flags[event] & STATUS_ROULETTE_SURVIVED)
        before_sampling = config.min_energy if survived else reflected
        weight = after / before_sampling if before_sampling > 0 else None
        reconstructed = base_pdf / mixture_pdf if mixture_pdf else None
        actual_base = actual_mixture = actual_weight = None
        records = sampling_records.get(local)
        if records is not None:
            slots, pdfs, weights = records
            position = int(np.searchsorted(slots, slot))
            if position < len(slots) and slots[position] == slot:
                actual_base, actual_mixture = map(float, pdfs[position])
                actual_weight = float(weights[position])
        matches_reconstruction = None
        if actual_base is not None and base_pdf is not None:
            matches_reconstruction = (
                math.isclose(actual_base, base_pdf, rel_tol=1e-7, abs_tol=1e-12)
                and math.isclose(actual_mixture, mixture_pdf, rel_tol=1e-7, abs_tol=1e-12)
            )
        rows.append(dict(face_index=face, scatter_model=profile.scatter_model,
            normal=normal.tolist(), incoming_direction=incoming.tolist(),
            outgoing_direction=outgoing.tolist() if outgoing is not None else None,
            effective_reflectance=reflected / before if before > 0 else None,
            incoming_packet_lumen=before, reflected_packet_lumen=reflected,
            roulette_survival_probability=reflected / config.min_energy if survived else 1.0,
            outgoing_packet_lumen=after, applied_sampling_weight=weight,
            base_direction_pdf_sr_inverse=base_pdf, receiver_direction_pdf_sr_inverse=receiver_pdf,
            mixed_direction_pdf_sr_inverse=mixture_pdf, receiver_mixture_fraction=applied_fraction,
            reconstructed_sampling_weight=reconstructed, pdf_matches_packet_weight=(
                math.isclose(weight, reconstructed, rel_tol=1e-7, abs_tol=1e-12)
                if weight is not None and reconstructed is not None else None),
            pdf_record_status=("reconstructed" if base_pdf is not None else
                "not_applicable_delta" if profile.scatter_model == "specular" and not profile.bsdf_asset_id else
                "missing_path_direction" if outgoing is None else
                "missing_bsdf_table" if profile.bsdf_asset_id else "unsupported_scatter_model"),
            sampling_pdf_capture_status=("captured" if actual_base is not None else
                "not_applied" if fraction == 0 else
                "missing_sampling_record" if base_pdf is not None else "not_applicable"),
            sampled_base_direction_pdf_sr_inverse=actual_base,
            sampled_mixed_direction_pdf_sr_inverse=actual_mixture,
            sampled_mis_weight=actual_weight,
            sampled_pdfs_match_reconstruction=matches_reconstruction,
            sampled_weight_matches_packet=(math.isclose(actual_weight, weight, rel_tol=1e-7, abs_tol=1e-12)
                if actual_weight is not None and weight is not None else None),
            pdf_evidence="reconstructed_from_path" if base_pdf is not None else (
                "delta" if profile.scatter_model == "specular" and not profile.bsdf_asset_id else "unavailable")))
        if outgoing is not None:
            incoming = outgoing
    return rows
