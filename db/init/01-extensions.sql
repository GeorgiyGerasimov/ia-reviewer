-- Runs once on first Postgres init. Enables pgvector so we can store
-- embeddings in the same database that holds LangGraph checkpoints.
CREATE EXTENSION IF NOT EXISTS vector;
