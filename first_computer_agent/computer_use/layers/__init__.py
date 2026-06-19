"""Five cascade layers for the Computer-Use skill.

Each layer module exposes:

    class <Name>Layer:
        name: ComputerUseLayer
        def try_(self, task: TaskSpec, recorder: Recorder) -> LayerOutcome:
            ...

The cascade orchestrator in `..skill` calls them in escalation order. A
layer that returns `applicable=False` is silently skipped; a layer that
returns `success=True` short-circuits the cascade.
"""

from .layer1_api import Layer1Api
from .layer2a_hotkeys import Layer2aHotkeys
from .layer2b_uia import Layer2bUia
from .layer2c_electron import Layer2cElectron
from .layer3_vision import Layer3Vision

__all__ = [
    "Layer1Api",
    "Layer2aHotkeys",
    "Layer2bUia",
    "Layer2cElectron",
    "Layer3Vision",
]
