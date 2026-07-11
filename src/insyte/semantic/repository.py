"""Load and save the editable ``semantic.yaml`` for a project."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from insyte.exceptions import ConfigError
from insyte.semantic.models import SemanticLayer


class SemanticRepository:
    """Reads and writes a project's semantic layer."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> SemanticLayer:
        if not self._path.exists():
            return SemanticLayer()
        raw = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        try:
            return SemanticLayer.model_validate(raw)
        except ValidationError as exc:
            raise ConfigError(f"Invalid semantic layer in {self._path}:\n{exc}") from exc

    def save(self, layer: SemanticLayer) -> None:
        data = layer.model_dump(mode="json", exclude_defaults=False)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
