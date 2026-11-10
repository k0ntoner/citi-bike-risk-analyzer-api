"""Contract of the PROCESS step."""

from dataclasses import dataclass

import pandas as pd

from bikeflow.contracts import raw_trips


@dataclass(frozen=True)
class ProcessInput:
    """Input of ``ProcessService.process``.

    Attributes:
        trips: Raw trips of the whole window, matching ``raw_trips`` schema
            (checked on creation). The same DataFrame is given to ANALYZE_RAW:
            read it, never modify it in place (work on a copy or derived frames).
    """

    trips: pd.DataFrame

    def __post_init__(self) -> None:
        raw_trips.validate(self.trips)
