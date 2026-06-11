"""Dagster resources for sharing state and connections."""

from dagster import ConfigurableResource
from sqlalchemy.orm import Session
from src.database import SessionLocal, init_db

class DatabaseResource(ConfigurableResource):
    """Resource for interacting with the PostgreSQL database."""
    
    def get_db(self) -> Session:
        return SessionLocal()

    def initialize_schema(self):
        init_db()
