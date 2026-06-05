#!/usr/bin/env bash
# Creates the `langfuse` database next to the app's `ia_reviewer` database
# inside the same Postgres instance. Runs once on first cluster init.
# Safe even when the observability profile is never used — empty DB just sits
# there. The Langfuse worker/web migrate the schema on their first boot.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE langfuse;
EOSQL
