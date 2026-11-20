"""File storage as seen by the steps."""

from contextlib import AbstractContextManager
from typing import BinaryIO, Protocol


class DatasetStore(Protocol):
    """File storage already limited to one folder, e.g. the folder of a run step.

    Keys are relative paths inside that folder, such as
    ``JC-202609-citibike-tripdata.csv.zip``. A step works only with this
    interface and does not know where the files actually live (local disk,
    cloud storage) or how the folders of runs are laid out.
    """

    def exists(self, key: str) -> bool:
        """Whether a file with this key is stored."""
        ...

    def open_write(self, key: str) -> AbstractContextManager[BinaryIO]:
        """Open a file for writing, replacing an existing one.

        The write is atomic: the file appears under ``key`` only when the
        ``with`` block exits normally. On an exception nothing is stored.
        """
        ...

    def open_read(self, key: str) -> AbstractContextManager[BinaryIO]:
        """Open a stored file for reading.

        Raises:
            FileNotFoundError: No file with this key.
        """
        ...
