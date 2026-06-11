"""Utility script to drop all tables and recreate them."""

import os
from dotenv import load_dotenv

load_dotenv()

from src.database import engine, Base, init_db

def reset_db():
    print("Dropping all tables...")
    Base.metadata.drop_all(bind=engine)
    print("Recreating tables...")
    init_db()
    print("Done.")

if __name__ == "__main__":
    reset_db()
