"""Local-disk implementation of ``DatasetStore``."""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import BinaryIO


class LocalDatasetStore:
    """Stores files under a folder on the local disk.

    Implements ``bikeflow.contracts.dataset_store.DatasetStore``.

    Args:
        root: Folder the store is limited to; created on the first write.
    """

    def __init__(self, root: str | os.PathLike) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        """Folder the store is limited to."""
        return self._root

    def exists(self, key: str) -> bool:
        """Whether a file with this key is stored."""
        return self._path(key).is_file()

    @contextmanager
    def open_write(self, key: str) -> Iterator[BinaryIO]:
        """Open a file for writing; it appears under ``key`` only on success.

        Writes to a temporary file next to the target and renames it at the
        end, so an interrupted write never leaves a partial file under ``key``.
        """
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with tmp.open("wb") as f:
                yield f
            os.replace(tmp, path)  # atomic on the same file system
        finally:
            tmp.unlink(missing_ok=True)

    @contextmanager
    def open_read(self, key: str) -> Iterator[BinaryIO]:
        """Open a stored file for reading.

        Raises:
            FileNotFoundError: No file with this key.
        """
        with self._path(key).open("rb") as f:
            yield f

    def _path(self, key: str) -> Path:
        """Resolve a key to a path, refusing keys that leave the root folder."""
        parts = PurePosixPath(key).parts
        if not parts or PurePosixPath(key).is_absolute() or ".." in parts:
            raise ValueError(f"Invalid key {key!r}: must be a relative path inside the store")
        return self._root.joinpath(*parts)
