import time

import numpy as np

from leakage_simulator.geometry import RayHitBatch
from tools.perf5e_native.device_replay import CONTRACT, DeviceSelectiveReplay
from tools.perf5e_native.probe import pack_rays
from tools.perf5e_native.selective_replay import SelectiveReplayObserver, assert_exact_query
from tools.perf5e_native.transport_validation import OptixTransportObserver


class DeviceReplayObserver(SelectiveReplayObserver):
    contract = CONTRACT

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.adapter = None
        self.adapter_scene = None

    def observe_with_backend(self, mesh, rays, reference, backend):
        if backend != "gpu_cuda":
            return super().observe_with_backend(mesh, rays, reference, backend)
        OptixTransportObserver.observe(self, mesh, rays, reference)
        if self.probe is None or not len(rays):
            return reference
        if self.adapter is None or self.adapter_scene is not self.scene or self.adapter.capacity < len(rays):
            if self.adapter is not None:
                self.adapter.close()
            self.adapter = None
            self.adapter_scene = None
            self.adapter = DeviceSelectiveReplay(mesh, self.directory, len(rays))
            self.adapter_scene = self.scene
        from numba import cuda

        packed = pack_rays(rays.origins, rays.directions, rays.min_t, rays.max_t, rays.ignore_faces)
        started = time.perf_counter()
        device_rays = cuda.to_device(packed, stream=self.adapter.stream)
        result = self.adapter.query_device(device_rays)
        corrected = RayHitBatch(result.distances.copy_to_host(), result.face_indices.copy_to_host())
        candidate = result.candidate.copy_to_host()
        replay = result.replay.copy_to_host().astype(np.bool_)
        rejected = self.adapter._arrays["rejected"][:len(rays)].copy_to_host().astype(np.bool_)
        record = self.records[-1]
        record.update(
            refinement_backend=backend, selective_seconds=time.perf_counter() - started,
            selective_timings=result.metadata["optix_timings"], device_adapter=result.metadata,
            harness_ray_upload_bytes=packed.nbytes,
            harness_hit_download_bytes=corrected.t.nbytes + corrected.face_indices.nbytes + candidate.nbytes + replay.nbytes + rejected.nbytes,
            replay_rows=int(replay.sum()), winner_only_rows=int((~replay).sum()),
            refinement_rejected_rows=int(rejected.sum()),
            corrected_face_mismatches=int(np.count_nonzero(corrected.face_indices != reference.face_indices)),
            corrected_distance_bit_mismatches=int(np.count_nonzero(corrected.t.view(np.uint64) != reference.t.view(np.uint64))),
        )
        if self.selective_capture is not None:
            record["selective_artifact"] = self.selective_capture(
                len(self.records) - 1, packed, candidate, corrected, replay, rejected, reference, backend,
            )
        assert_exact_query(corrected, reference)
        return corrected

    def summary(self):
        summary = super().summary()
        device_records = [record["device_adapter"] for record in self.records if "device_adapter" in record]
        summary.update(device_adapter_calls=len(device_records),
                       device_adapter_rows=sum(record["checked_rows"] for record in device_records),
                       device_adapter_scope="device_query_inside_host_roundtrip_harness_not_fused_transport")
        return summary

    def close(self):
        try:
            if self.adapter is not None:
                self.adapter.close()
                self.adapter = None
                self.adapter_scene = None
        finally:
            super().close()
