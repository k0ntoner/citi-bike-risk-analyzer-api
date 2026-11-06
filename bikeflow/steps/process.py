"""PROCESS step: cleaning and aggregation into the training dataset."""

from bikeflow.contracts.process import ProcessInput


class ProcessService:
    """Turns raw trips into the dataset the model trains on.

    Works on the raw trips independently of the analysis steps and applies its
    own validation, including checks for problems absent from the current data
    (duplicates, end before start, DST). Aggregates trips into hourly
    per-station departures and arrivals, including zero hours, and joins the
    features (time, station, weather). Returns the dataset with a manifest of
    its feature and target columns.
    """

    def process(self, step_input: ProcessInput) -> None:
        """Run the step. The output contract is not defined yet (TBD)."""
        raise NotImplementedError("PROCESS contract is not defined yet")
