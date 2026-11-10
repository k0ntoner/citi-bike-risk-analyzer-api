"""TRAIN step: model training and evaluation."""


class TrainService:
    """Trains the empty/full station risk model on the processed dataset.

    Splits the data by time, trains and evaluates the model, and returns the
    serialized model together with its parameters and metrics.
    """

    def train(self) -> None:
        """Run the step. Input and output are defined by the step contract (TBD)."""
        raise NotImplementedError("TRAIN contract is not defined yet")
