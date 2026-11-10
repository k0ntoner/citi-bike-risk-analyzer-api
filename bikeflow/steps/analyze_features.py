"""ANALYZE_FEATURES step: statistics of the train-ready dataset for the UI."""


class AnalyzeFeaturesService:
    """Describes the dataset produced by the PROCESS step without changing it.

    Purely informational, like ANALYZE_RAW. Computes target and feature
    distributions and correlations. Returns a structured description (tables and
    values, never HTML) from which the app builds charts and tables.
    """

    def analyze_features(self) -> None:
        """Run the step. Input and output are defined by the step contract (TBD)."""
        raise NotImplementedError("ANALYZE_FEATURES contract is not defined yet")
