"""INGEST step: download of the monthly trip datasets."""


class IngestService:
    """Downloads the trip history files that fall into the rolling window.

    Lists the published monthly files in the source bucket, picks the latest
    ``window_months`` of them and downloads the ones that are not stored yet.
    Reports the dataset keys and the actual period they cover, which may be
    shorter than requested if some months are not published.
    """

    def ingest(self) -> None:
        """Run the step. Input and output are defined by the step contract (TBD)."""
        raise NotImplementedError("INGEST contract is not defined yet")
