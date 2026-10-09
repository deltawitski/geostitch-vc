from __future__ import annotations

import json
from pathlib import Path

from geostitch.models import Project


def save_project(project: Project, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(project.to_dict(), indent=2), encoding="utf-8")


def load_project(path: str | Path) -> Project:
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("The project file must contain a JSON object.")
    return Project.from_dict(data)
