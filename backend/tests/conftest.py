import os

# Ensure tests never pick up a developer's real .env values for these.
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://nextix:nextix@localhost:5432/nextix")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
