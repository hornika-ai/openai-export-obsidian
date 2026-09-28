"""Strict macOS ``.DS_Store`` hygiene for emitted packs.

This module never reads the contents of a ``.DS_Store`` file.  It uses
``lstat``-equivalent metadata inspection so a regular Finder artifact can be
handled narrowly while a symlink, directory, or special entry with the same
name remains a hard error.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator


DS_STORE_NAME = ".DS_Store"
DS_STORE_DIAGNOSTIC = "macos_ds_store_contamination"
QUARANTINE_CONFIRMATION = "QUARANTINE_DS_STORE"


class DsStoreHygieneError(ValueError):
    """A safe, path-free error for the quarantine maintenance boundary."""


@dataclass(frozen=True)
class TreeInventory:
    regular_files: int
    directories: int
    symlinks: int
    special_entries: int

    def as_dict(self) -> dict[str, int]:
        return {
            "regular_files": self.regular_files,
            "directories": self.directories,
            "symlinks": self.symlinks,
            "special_entries": self.special_entries,
        }


@dataclass(frozen=True)
class DsStoreScan:
    regular_files: tuple[Path, ...]
    directories: int
    symlinks: int
    special_entries: int
    inventory: TreeInventory

    @property
    def has_unsafe_targets(self) -> bool:
        return bool(self.directories or self.symlinks or self.special_entries)

    def categories(self) -> dict[str, int]:
        return {
            "regular_files": len(self.regular_files),
            "directories": self.directories,
            "symlinks": self.symlinks,
            "special_entries": self.special_entries,
        }


@dataclass(frozen=True)
class PermissionAssessment:
    non_target_entries_private: bool
    target_files_with_group_other_bits: int


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    size: int
    modified_ns: int


def is_ds_store_name(name: str) -> bool:
    """Return true only for the single Finder metadata filename."""
    return name == DS_STORE_NAME


def _entry_kind(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "regular"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "special"


def _lstat(path: Path) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise DsStoreHygieneError("metadata inspection failed") from exc


def _require_directory(path: Path, *, label: str) -> None:
    kind = _entry_kind(_lstat(path).st_mode)
    if kind != "directory":
        raise DsStoreHygieneError(f"{label} must be a non-symlinked directory")


def _walk_metadata(root: Path) -> Iterator[tuple[Path, os.stat_result]]:
    """Yield entries recursively without following links or opening files."""
    _require_directory(root, label="pack")
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as entries:
                ordered = sorted(entries, key=lambda entry: entry.name)
        except OSError as exc:
            raise DsStoreHygieneError("metadata inspection failed") from exc
        for entry in ordered:
            path = Path(entry.path)
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise DsStoreHygieneError("metadata inspection failed") from exc
            yield path, entry_stat
            if stat.S_ISDIR(entry_stat.st_mode):
                stack.append(path)


def scan_ds_store_entries(pack: Path | str) -> DsStoreScan:
    """Return a metadata-only inventory of exact ``.DS_Store`` entries."""
    pack = Path(pack)
    regular: list[Path] = []
    directories = symlinks = special_entries = 0
    inventory = {"regular": 0, "directory": 1, "symlink": 0, "special": 0}
    for path, entry_stat in _walk_metadata(pack):
        kind = _entry_kind(entry_stat.st_mode)
        inventory[kind] += 1
        if not is_ds_store_name(path.name):
            continue
        if kind == "regular":
            regular.append(path)
        elif kind == "directory":
            directories += 1
        elif kind == "symlink":
            symlinks += 1
        else:
            special_entries += 1
    return DsStoreScan(
        regular_files=tuple(sorted(regular, key=lambda path: path.as_posix())),
        directories=directories,
        symlinks=symlinks,
        special_entries=special_entries,
        inventory=TreeInventory(
            regular_files=inventory["regular"],
            directories=inventory["directory"],
            symlinks=inventory["symlink"],
            special_entries=inventory["special"],
        ),
    )


def ds_store_diagnostics(pack: Path | str) -> list[str]:
    """Return strict, machine-readable diagnostics without exposing paths."""
    try:
        scan = scan_ds_store_entries(pack)
    except DsStoreHygieneError as exc:
        return [f"{DS_STORE_DIAGNOSTIC}:metadata_scan_failed"]
    categories = scan.categories()
    return [
        f"{DS_STORE_DIAGNOSTIC}:{category}={count}"
        for category, count in categories.items()
        if count
    ]


def remove_residual_ds_store_from_new_output(pack: Path | str) -> int:
    """Remove only regular residual Finder files from a newly generated output.

    The full scan completes before any unlink.  A non-regular entry with the
    reserved name aborts without mutation, so it cannot be hidden as harmless
    operating-system metadata.
    """
    scan = scan_ds_store_entries(pack)
    if scan.has_unsafe_targets:
        raise DsStoreHygieneError("new output contains a non-regular .DS_Store entry")
    for path in scan.regular_files:
        # Re-check immediately before mutation.  ``unlink`` removes the entry,
        # never opens or decodes its contents.
        if _entry_kind(_lstat(path).st_mode) != "regular":
            raise DsStoreHygieneError(".DS_Store target changed during cleanup")
    for path in scan.regular_files:
        try:
            path.unlink()
        except OSError as exc:
            raise DsStoreHygieneError("unable to remove residual .DS_Store") from exc
    return len(scan.regular_files)


def _is_fixed_macos_system_alias(path: Path) -> bool:
    """Allow only the two standard macOS lexical aliases used by temp paths."""
    if path not in {Path("/var"), Path("/tmp")}:
        return False
    try:
        target = os.readlink(path)
        return target.startswith(("/private/", "private/"))
    except OSError:
        return False


def _has_symlink_ancestor(path: Path) -> bool:
    """Reject caller-controlled symlink components without resolving them."""
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise DsStoreHygieneError("metadata inspection failed") from exc
        if stat.S_ISLNK(mode) and not _is_fixed_macos_system_alias(current):
            return True
    return False


def _is_inside_git_worktree(path: Path) -> bool:
    absolute = Path(os.path.abspath(path))
    for parent in (absolute, *absolute.parents):
        git_marker = parent / ".git"
        try:
            git_marker.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise DsStoreHygieneError("metadata inspection failed") from exc
        return True
    return False


def _identity(entry_stat: os.stat_result) -> FileIdentity:
    return FileIdentity(
        device=entry_stat.st_dev,
        inode=entry_stat.st_ino,
        size=entry_stat.st_size,
        modified_ns=entry_stat.st_mtime_ns,
    )


def _permission_assessment(pack: Path, targets: tuple[Path, ...]) -> PermissionAssessment:
    target_set = set(targets)
    non_target_private = not bool(stat.S_IMODE(_lstat(pack).st_mode) & 0o077)
    target_with_group_other_bits = 0
    for path, entry_stat in _walk_metadata(pack):
        if path in target_set:
            if stat.S_IMODE(entry_stat.st_mode) & 0o077:
                target_with_group_other_bits += 1
        elif stat.S_IMODE(entry_stat.st_mode) & 0o077:
            non_target_private = False
    return PermissionAssessment(
        non_target_entries_private=non_target_private,
        target_files_with_group_other_bits=target_with_group_other_bits,
    )


def _official_regular_file_snapshot(pack: Path, targets: tuple[Path, ...]) -> dict[Path, FileIdentity]:
    target_set = set(targets)
    return {
        path: _identity(entry_stat)
        for path, entry_stat in _walk_metadata(pack)
        if path not in target_set and _entry_kind(entry_stat.st_mode) == "regular"
    }


def _target_identity_snapshot(targets: tuple[Path, ...]) -> dict[Path, FileIdentity]:
    return {path: _identity(_lstat(path)) for path in targets}


def quarantine_ds_store(
    pack: Path | str,
    quarantine: Path | str,
    *,
    confirmation: str | None,
    dry_run: bool = False,
    _before_mutation_hook: Callable[[], None] | None = None,
) -> dict[str, object]:
    """Move exact regular ``.DS_Store`` entries to a new external quarantine.

    This is deliberately a maintenance operation, not pack validation and not
    regeneration.  Its report is sanitized: no source or destination paths are
    returned.
    """
    pack = Path(pack)
    quarantine = Path(quarantine)
    _require_directory(pack, label="pack")
    if _has_symlink_ancestor(pack) or _has_symlink_ancestor(quarantine.parent):
        raise DsStoreHygieneError("pack and quarantine parent must not use symlinks")
    if quarantine.exists() or quarantine.is_symlink():
        raise DsStoreHygieneError("quarantine destination must be new")
    _require_directory(quarantine.parent, label="quarantine parent")
    pack_absolute = Path(os.path.abspath(pack))
    quarantine_absolute = Path(os.path.abspath(quarantine))
    if quarantine_absolute == pack_absolute or pack_absolute in quarantine_absolute.parents or quarantine_absolute in pack_absolute.parents:
        raise DsStoreHygieneError("quarantine destination must be external to the pack")
    if _is_inside_git_worktree(pack) or _is_inside_git_worktree(quarantine.parent):
        raise DsStoreHygieneError("pack and quarantine destination must be outside Git worktrees")
    if _lstat(pack).st_dev != _lstat(quarantine.parent).st_dev:
        raise DsStoreHygieneError("quarantine destination must share the pack filesystem")
    scan = scan_ds_store_entries(pack)
    if scan.has_unsafe_targets:
        raise DsStoreHygieneError("non-regular .DS_Store target blocks quarantine")
    permissions = _permission_assessment(pack, scan.regular_files)
    if not permissions.non_target_entries_private:
        raise DsStoreHygieneError("pack permissions are not private")
    result: dict[str, object] = {
        "status": "dry_run" if dry_run else "pending_confirmation",
        "targets": scan.categories(),
        "inventory": scan.inventory.as_dict(),
        "non_target_entries_private": permissions.non_target_entries_private,
        "target_files_with_group_other_bits": permissions.target_files_with_group_other_bits,
        "official_regular_file_identity_comparison": "not_performed",
        "moved_target_identity_comparison": "not_performed",
        "remaining_ds_store_regular_files": len(scan.regular_files),
    }
    if dry_run:
        return result
    if confirmation != QUARANTINE_CONFIRMATION:
        raise DsStoreHygieneError("explicit quarantine confirmation is required")

    before_official = _official_regular_file_snapshot(pack, scan.regular_files)
    before_targets = _target_identity_snapshot(scan.regular_files)
    if _before_mutation_hook is not None:
        _before_mutation_hook()

    # Re-check all lexical, filesystem, and metadata invariants immediately
    # before the first mutation.  No link is ever resolved to authorize a move.
    if _has_symlink_ancestor(pack) or _has_symlink_ancestor(quarantine.parent):
        raise DsStoreHygieneError("pack and quarantine parent must not use symlinks")
    if quarantine.exists() or quarantine.is_symlink():
        raise DsStoreHygieneError("quarantine destination changed before quarantine")
    _require_directory(pack, label="pack")
    _require_directory(quarantine.parent, label="quarantine parent")
    if _lstat(pack).st_dev != _lstat(quarantine.parent).st_dev:
        raise DsStoreHygieneError("quarantine destination must share the pack filesystem")
    current = scan_ds_store_entries(pack)
    if current != scan or current.has_unsafe_targets:
        raise DsStoreHygieneError(".DS_Store inventory changed before quarantine")
    if _permission_assessment(pack, current.regular_files) != permissions:
        raise DsStoreHygieneError("pack permissions changed before quarantine")
    if _official_regular_file_snapshot(pack, current.regular_files) != before_official:
        raise DsStoreHygieneError("official regular entries changed before quarantine")
    if _target_identity_snapshot(current.regular_files) != before_targets:
        raise DsStoreHygieneError(".DS_Store targets changed before quarantine")
    try:
        quarantine.mkdir(mode=0o700)
        quarantine.chmod(0o700)
    except OSError as exc:
        raise DsStoreHygieneError("unable to create quarantine") from exc
    for index, source in enumerate(scan.regular_files, start=1):
        source_stat = _lstat(source)
        if _entry_kind(source_stat.st_mode) != "regular" or _identity(source_stat) != before_targets[source]:
            raise DsStoreHygieneError(".DS_Store target changed during quarantine")
        destination = quarantine / f"{index:04d}.DS_Store"
        try:
            os.replace(source, destination)
            destination.chmod(0o600)
        except OSError as exc:
            raise DsStoreHygieneError("unable to move .DS_Store into quarantine") from exc
    remaining = scan_ds_store_entries(pack)
    if remaining.regular_files or remaining.has_unsafe_targets:
        raise DsStoreHygieneError(".DS_Store remains after quarantine")
    after_permissions = _permission_assessment(pack, ())
    if not after_permissions.non_target_entries_private:
        raise DsStoreHygieneError("pack permissions changed during quarantine")
    after_official = _official_regular_file_snapshot(pack, ())
    if after_official != before_official:
        raise DsStoreHygieneError("official regular entries changed during quarantine")
    moved_targets_preserved = all(
        _identity(_lstat(quarantine / f"{index:04d}.DS_Store")) == before_targets[source]
        for index, source in enumerate(scan.regular_files, start=1)
    )
    if not moved_targets_preserved:
        raise DsStoreHygieneError("moved .DS_Store identity changed during quarantine")
    return {
        "status": "quarantined",
        "targets": scan.categories(),
        "inventory": scan.inventory.as_dict(),
        "moved_regular_files": len(scan.regular_files),
        "remaining_ds_store_regular_files": 0,
        "non_target_entries_private": True,
        "target_files_with_group_other_bits": permissions.target_files_with_group_other_bits,
        "official_regular_file_identity_unchanged": True,
        "moved_target_identity_preserved": True,
    }
