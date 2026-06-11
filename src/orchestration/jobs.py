"""Dagster jobs and definitions."""

from dagster import (
    AssetSelection,
    Definitions,
    ScheduleDefinition,
    define_asset_job,
    load_assets_from_modules,
)

from src.orchestration import assets, resources

# Load all assets from the assets module
all_assets = load_assets_from_modules([assets])

# Define a job that materializes all assets
ingestion_job = define_asset_job(
    name="full_ingestion_job",
    selection=AssetSelection.groups("bronze", "silver", "gold", "news", "analytics"),
)

# Define a daily schedule for the ingestion job
daily_ingestion_schedule = ScheduleDefinition(
    job=ingestion_job,
    cron_schedule="0 0 * * *", # Daily at midnight
)

# Core Dagster definitions object
defs = Definitions(
    assets=all_assets,
    jobs=[ingestion_job],
    schedules=[daily_ingestion_schedule],
    resources={
        "db_resource": resources.DatabaseResource(),
    },
)

# Export for easier reconstruction by name
job = defs.get_job_def("full_ingestion_job")
