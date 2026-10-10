from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from geostitch.models import Project


def _atomic_write(destination: Path, content: bytes) -> None:
    descriptor, temporary_path = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    temporary = Path(temporary_path)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def save_project(project: Project, path: str | Path, *, backup: bool = True) -> None:
    destination = Path(path)
    content = json.dumps(project.to_dict(), indent=2).encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if backup and destination.is_file():
        _atomic_write(destination.with_suffix(destination.suffix + ".bak"), destination.read_bytes())
    _atomic_write(destination, content)


def load_project(path: str | Path) -> Project:
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("The project file must contain a JSON object.")
    return Project.from_dict(data)


def save_recovery(project: Project, path: str | Path, source_path: Path | None) -> None:
    destination = Path(path)
    data = project.to_dict()
    data["recovery_source"] = str(source_path.resolve()) if source_path is not None else None
    content = json.dumps(data, indent=2).encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(destination, content)


def load_recovery(path: str | Path) -> tuple[Project, Path | None]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "recovery_source" not in data:
        raise ValueError("The file is not a recovery snapshot.")
    source = data["recovery_source"]
    if source is not None and not isinstance(source, str):
        raise ValueError("The recovery source path must be a string or null.")
    return Project.from_dict(data), Path(source) if source else None
