"""Project settings. Every value can be overridden with an environment variable."""

import os

# GBFS discovery feed (real-time station status) for Citi Bike NYC.
GBFS_URL = os.environ.get("BIKEFLOW_GBFS_URL", "https://gbfs.lyft.com/gbfs/1.1/bkn/gbfs.json")

# Bucket with monthly trip history files.
TRIPDATA_URL = os.environ.get("BIKEFLOW_TRIPDATA_URL", "https://s3.amazonaws.com/tripdata")

# Monthly trip file name, e.g. JC-202609-citibike-tripdata.csv.zip; only the month changes.
TRIPDATA_FILE_REGEX = os.environ.get(
    "BIKEFLOW_TRIPDATA_FILE_REGEX", r"^JC-(?P<month>\d{6})-citibike-tripdata\.csv\.zip$"
)

# Rolling window: how many latest published months of trips we use.
# It moves forward by itself when a new monthly file appears.
TRIPDATA_WINDOW_MONTHS = int(os.environ.get("BIKEFLOW_TRIPDATA_WINDOW_MONTHS", "12"))
