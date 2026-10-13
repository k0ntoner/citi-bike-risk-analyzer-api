"""ANALYZE_RAW step: statistics of the raw trip dataset for the UI."""

from bikeflow.contracts.analyze_raw import AnalyzeRawInput


class AnalyzeRawService:
    """Describes the state of the downloaded trips without changing them.

    Purely informational: no other step depends on its result. Finds anomalies
    (duplicates, missing end stations, too long rides, quick round trips),
    inactive stations and incomplete station history, and computes distributions.
    Returns a structured description (tables and values, never HTML) from which
    the app builds charts and tables.
    """

    def analyze_raw(self, step_input: AnalyzeRawInput) -> None:
        """Run the step. The output contract is not defined yet (TBD)."""
        raise NotImplementedError("ANALYZE_RAW contract is not defined yet")
