from __future__ import annotations

import math
import os
from typing import Any, Mapping

import numpy as np


def create_device_views(device: Any, layout):
    mode = os.environ.get('LEAKAGE_GPU_TRANSFER_VIEWS', 'direct')
    if mode not in ('sliced', 'direct'):
        raise ValueError('GPU transfer views must use sliced or direct metadata')
    for start, end, dtype, shape in layout.values():
        if start < 0 or end < start or end > device.nbytes:
            raise ValueError('GPU transfer view exceeds its owning buffer')
        if dtype.hasobject or math.prod(shape) * dtype.itemsize != end - start:
            raise ValueError('GPU transfer view shape does not match its byte range')
        if any(dimension < 0 for dimension in shape) or start % max(1, dtype.alignment):
            raise ValueError('GPU transfer view shape or alignment is invalid')
    if mode == 'sliced' or not hasattr(device, 'gpu_data'):
        return {name: device[start:end].view(dtype).reshape(shape)
            for name, (start, end, dtype, shape) in layout.items()}
    from numba.cuda.cudadrv.devicearray import DeviceNDArray
    views = {}
    for name, (start, end, dtype, shape) in layout.items():
        if end == start:
            strides = np.empty(0, dtype=dtype).reshape(shape).strides
        else:
            stride = dtype.itemsize
            strides = []
            for dimension in reversed(shape):
                strides.append(stride)
                stride *= dimension
            strides = tuple(reversed(strides))
        views[name] = DeviceNDArray(shape, strides, dtype, stream=device.stream,
            gpu_data=device.gpu_data.view(start, end))
    return views


def upload_aligned_arrays(cuda: Any, arrays: Mapping[str, np.ndarray]):
    layout = {}
    byte_count = 0
    for name, values in arrays.items():
        if values.dtype.hasobject:
            raise ValueError('GPU transfers require fixed-width numeric arrays')
        alignment = max(256, values.dtype.alignment)
        byte_count += -byte_count % alignment
        end = byte_count + values.nbytes
        layout[name] = (byte_count, end, values.dtype, values.shape)
        byte_count = end
    host = np.zeros(byte_count, dtype=np.uint8)
    for name, values in arrays.items():
        start, end, dtype, shape = layout[name]
        np.copyto(host[start:end].view(dtype).reshape(shape), values, casting='no')
    device = cuda.to_device(host)
    views = create_device_views(device, layout)
    return device, views
