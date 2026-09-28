from __future__ import annotations

import hashlib
from importlib.resources import files


MANAGED_TEMPLATES = {
    "Parsing Data Explorer.md": "Parsing Data Explorer.md",
    "app/view.js": "view.js",
    "app/view.css": "view.css",
}


def template_bytes() -> dict[str, bytes]:
    root = files("openai_export_obsidian").joinpath("templates", "analytics")
    return {destination: root.joinpath(source).read_bytes() for destination, source in MANAGED_TEMPLATES.items()}


def template_hashes() -> dict[str, str]:
    return {path: hashlib.sha256(content).hexdigest() for path, content in template_bytes().items()}
