"""Pipeline steps.

Each step is independent: it gets everything it needs through its method
arguments and returns its result. Steps must not import ``bikeflow.storage``,
``bikeflow.service`` or ``bikeflow.config``; the orchestrator does all
persistence and passes the configuration in.
"""
