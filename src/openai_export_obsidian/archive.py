from __future__ import annotations

import hashlib
import shutil
import zipfile
from pathlib import Path
from typing import Callable

from .inventory import ExportInventory, InventoryEntry
from .models import ArchiveMember


class ExportArchive:
    """Compatibility adapter over the canonical recursive ``ExportInventory``.

    Existing parser modules keep their ``ArchiveMember`` API while all archive
    discovery is delegated to ``ExportInventory``. New stages should consume the
    inventory directly when they need canonical member metadata.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        inventory: ExportInventory | None = None,
        forensic_hashes: bool = False,
    ):
        self.path = Path(path)
        self.inventory = inventory or ExportInventory.scan(str(self.path), forensic_hashes=forensic_hashes)

    def iter_members(self) -> list[ArchiveMember]:
        return self.members_from_inventory(lambda entry: True)

    def find_members(self, predicate: Callable[[ArchiveMember], bool]) -> list[ArchiveMember]:
        return [member for member in self.iter_members() if predicate(member)]

    def find_inventory_entries(self, predicate: Callable[[InventoryEntry], bool]) -> list[InventoryEntry]:
        return self.inventory.find_entries(predicate)

    def members_from_inventory(self, predicate: Callable[[InventoryEntry], bool]) -> list[ArchiveMember]:
        return [
            self._archive_member(entry)
            for entry in self.inventory.entries
            if not entry.is_directory and predicate(entry)
        ]

    def read_bytes(self, member: ArchiveMember) -> bytes:
        with zipfile.ZipFile(self.path) as outer:
            return self._read_from_archive(outer, self._member_chain(member), member.inner_name or member.name)

    def read_text(self, member: ArchiveMember) -> str:
        return self.read_bytes(member).decode("utf-8")

    def copy_member(self, member: ArchiveMember, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(self.path) as outer, destination.open("wb") as dst:
            self._copy_from_archive(outer, self._member_chain(member), member.inner_name or member.name, dst)

    def copy_member_and_hash(self, member: ArchiveMember, destination: Path) -> str:
        """Copy one member once while calculating its SHA-256."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with zipfile.ZipFile(self.path) as outer, destination.open("wb") as dst:
            self._copy_and_hash_from_archive(outer, self._member_chain(member), member.inner_name or member.name, dst, digest)
        return digest.hexdigest()

    def hash_member(self, member: ArchiveMember) -> str:
        """Return a SHA-256 digest for one archive member without extracting it.

        This is intentionally separate from ``ExportInventory(forensic_hashes=True)``:
        payload-equivalence classification needs hashes for physical payloads only,
        not every metadata or conversation member in the export.
        """
        digest = hashlib.sha256()
        with zipfile.ZipFile(self.path) as outer:
            self._hash_from_archive(outer, self._member_chain(member), member.inner_name or member.name, digest)
        return digest.hexdigest()

    def physical_asset_members(self) -> list[ArchiveMember]:
        return [self._archive_member(entry) for entry in self.inventory.physical_entries()]

    def _read_from_archive(
        self,
        archive: zipfile.ZipFile,
        chain: tuple[str, ...],
        member_name: str,
    ) -> bytes:
        if not chain:
            return archive.read(member_name)
        with archive.open(chain[0]) as nested_file:
            with zipfile.ZipFile(nested_file) as nested:
                return self._read_from_archive(nested, chain[1:], member_name)

    def _copy_from_archive(
        self,
        archive: zipfile.ZipFile,
        chain: tuple[str, ...],
        member_name: str,
        destination,
    ) -> None:
        if not chain:
            with archive.open(member_name) as src:
                shutil.copyfileobj(src, destination)
            return
        with archive.open(chain[0]) as nested_file:
            with zipfile.ZipFile(nested_file) as nested:
                self._copy_from_archive(nested, chain[1:], member_name, destination)

    def _copy_and_hash_from_archive(
        self,
        archive: zipfile.ZipFile,
        chain: tuple[str, ...],
        member_name: str,
        destination,
        digest,
    ) -> None:
        if not chain:
            with archive.open(member_name) as src:
                while chunk := src.read(1024 * 1024):
                    destination.write(chunk)
                    digest.update(chunk)
            return
        with archive.open(chain[0]) as nested_file:
            with zipfile.ZipFile(nested_file) as nested:
                self._copy_and_hash_from_archive(nested, chain[1:], member_name, destination, digest)

    def _hash_from_archive(
        self,
        archive: zipfile.ZipFile,
        chain: tuple[str, ...],
        member_name: str,
        digest: "hashlib._Hash",
    ) -> None:
        if not chain:
            with archive.open(member_name) as src:
                while chunk := src.read(1024 * 1024):
                    digest.update(chunk)
            return
        with archive.open(chain[0]) as nested_file:
            with zipfile.ZipFile(nested_file) as nested:
                self._hash_from_archive(nested, chain[1:], member_name, digest)

    @staticmethod
    def _member_chain(member: ArchiveMember) -> tuple[str, ...]:
        if member.nested_chain:
            return member.nested_chain
        if member.nested_zip:
            return (member.nested_zip,)
        return ()

    @staticmethod
    def _archive_member(entry: InventoryEntry) -> ArchiveMember:
        chain = entry.archive_chain
        return ArchiveMember(
            outer_zip=entry.outer_archive,
            name=entry.member_path,
            size=entry.size,
            nested_zip=chain[0] if chain else None,
            inner_name=entry.member_path if chain else None,
            nested_chain=chain,
        )
