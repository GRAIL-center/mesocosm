"""The small Torch/NNsight surface used by optional activation recompute.

These structural types keep the control-plane installation type-checkable without installing
GPU libraries. Recomputer loads the real packages only when recompute is requested.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Protocol


class TraceTensor(Protocol):
    def __getitem__(self, index: int) -> TraceTensor: ...
    def save(self) -> TraceTensor: ...
    def detach(self) -> TraceTensor: ...
    def to(self, dtype: object) -> TraceTensor: ...
    def cpu(self) -> TraceTensor: ...
    def argmax(self, dim: int) -> TraceTensor: ...
    def tolist(self) -> object: ...


class CudaMemory(Protocol):
    def reset_peak_memory_stats(self) -> None: ...
    def max_memory_allocated(self) -> int: ...


class TorchAPI(Protocol):
    float16: object
    cuda: CudaMemory

    def tensor(self, data: list[list[int]], *, device: str) -> object: ...
    def ones_like(self, tensor: object) -> object: ...
    def no_grad(self) -> AbstractContextManager[object]: ...


class TraceLayer(Protocol):
    output: TraceTensor | tuple[TraceTensor, ...] | list[TraceTensor]


class ModelLayers(Protocol):
    layers: Sequence[TraceLayer]


class ModelOutput(Protocol):
    logits: TraceTensor


class TraceModel(Protocol):
    model: ModelLayers
    output: ModelOutput

    def trace(self, inputs: dict[str, object]) -> AbstractContextManager[object]: ...


class ModelFactory(Protocol):
    def __call__(self, model_id: str, *, dispatch: bool, torch_dtype: object, device_map: str) -> TraceModel: ...
