"""Contract of the ANALYZE_RAW step."""

from dataclasses import dataclass

import pandas as pd

from bikeflow.contracts import raw_trips


@dataclass(frozen=True)
class AnalyzeRawInput:
    """Input of ``AnalyzeRawService.analyze_raw``.

    Attributes:
        trips: Raw trips of the whole window, matching ``raw_trips`` schema
            (checked on creation). The same DataFrame is given to PROCESS:
            read it, never modify it in place.
    """

    trips: pd.DataFrame

    def __post_init__(self) -> None:
        raw_trips.validate(self.trips)
