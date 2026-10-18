"""Project settings. Every value can be overridden with an environment variable."""

import os

# GBFS discovery feed (real-time station status) for Citi Bike NYC.
GBFS_URL = os.environ.get("BIKEFLOW_GBFS_URL", "https://gbfs.lyft.com/gbfs/1.1/bkn/gbfs.json")

# Bucket with monthly trip history files.
TRIPDATA_URL = os.environ.get("BIKEFLOW_TRIPDATA_URL", "https://s3.amazonaws.com/tripdata")

# Monthly trip file name, e.g. JC-202609-citibike-tripdata.csv.zip; only the month changes.
# Some months are published without ".csv" (JC-202510-citibike-tripdata.zip).
TRIPDATA_FILE_REGEX = os.environ.get(
    "BIKEFLOW_TRIPDATA_FILE_REGEX", r"^JC-(?P<month>\d{6})-citibike-tripdata(\.csv)?\.zip$"
)

# Rolling window: how many latest published months of trips we use.
# It moves forward by itself when a new monthly file appears.
TRIPDATA_WINDOW_MONTHS = int(os.environ.get("BIKEFLOW_TRIPDATA_WINDOW_MONTHS", "12"))

# Folder with all local files of the pipeline (git-ignored); runs live in {DATA_ROOT}/runs/.
DATA_ROOT = os.environ.get("BIKEFLOW_DATA_ROOT", "data")

# PostgreSQL connection (SQLAlchemy URL with the psycopg 3 driver).
# The default points to a local development database.
DATABASE_URL = os.environ.get(
    "BIKEFLOW_DATABASE_URL", "postgresql+psycopg://postgres@localhost:5432/bikeflow"
)

# Apply pending SQL migrations (migrations/*.sql) when the app starts.
MIGRATE_ON_STARTUP = os.environ.get("BIKEFLOW_MIGRATE_ON_STARTUP", "true").lower() == "true"
