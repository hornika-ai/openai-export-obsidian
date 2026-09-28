from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class AnalyticsPathError(ValueError):
    pass


@dataclass(frozen=True)
class AnalyticsPaths:
    pack: Path
    output: Path
    vault: Path
    pack_vault_path: str
    output_vault_path: str


def resolve_analytics_paths(pack: Path, output: Path) -> AnalyticsPaths:
    if pack.is_symlink() or output.is_symlink():
        raise AnalyticsPathError("pack and output paths must not be symlinks")
    try:
        resolved_pack = pack.resolve(strict=True)
    except OSError as exc:
        raise AnalyticsPathError(f"pack path cannot be resolved: {exc}") from exc
    if not resolved_pack.is_dir():
        raise AnalyticsPathError("pack path is not a directory")
    resolved_output = output.resolve(strict=False)
    if resolved_output.exists() and not resolved_output.is_dir():
        raise AnalyticsPathError("output path exists and is not a directory")
    if resolved_output == resolved_pack:
        raise AnalyticsPathError("pack and output must be distinct")
    if _is_relative_to(resolved_output, resolved_pack) or _is_relative_to(resolved_pack, resolved_output):
        raise AnalyticsPathError("pack and output must not contain one another")

    common = Path(os.path.commonpath((resolved_pack, resolved_output)))
    vault = next((candidate for candidate in (common, *common.parents) if (candidate / ".obsidian").is_dir()), None)
    if vault is None:
        raise AnalyticsPathError("pack and output do not share an Obsidian vault root")
    try:
        pack_relative = resolved_pack.relative_to(vault).as_posix()
        output_relative = resolved_output.relative_to(vault).as_posix()
    except ValueError as exc:
        raise AnalyticsPathError("pack and output must remain below the common vault root") from exc
    if not pack_relative or not output_relative:
        raise AnalyticsPathError("pack and output cannot be the vault root")
    return AnalyticsPaths(
        pack=resolved_pack,
        output=resolved_output,
        vault=vault,
        pack_vault_path=pack_relative,
        output_vault_path=output_relative,
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
