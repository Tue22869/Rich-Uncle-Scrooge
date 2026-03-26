"""Shared test fixtures for database isolation."""
import os

# Force a test-only in-memory database BEFORE any imports touch db.session
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
