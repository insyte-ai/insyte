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
        self._cached_signature: tuple[int, int] | None = None
        self._cached_layer: SemanticLayer | None = None

    def load(self) -> SemanticLayer:
        if not self._path.exists():
            return SemanticLayer()
        stat = self._path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        if signature == self._cached_signature and self._cached_layer is not None:
            return self._cached_layer.model_copy(deep=True)
        raw = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        try:
            layer = SemanticLayer.model_validate(raw)
        except ValidationError as exc:
            raise ConfigError(f"Invalid semantic layer in {self._path}:\n{exc}") from exc
        self._cached_signature = signature
        self._cached_layer = layer
        return layer.model_copy(deep=True)

    def save(self, layer: SemanticLayer) -> None:
        data = layer.model_dump(mode="json", exclude_defaults=False)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        stat = self._path.stat()
        self._cached_signature = (stat.st_mtime_ns, stat.st_size)
        self._cached_layer = layer.model_copy(deep=True)
