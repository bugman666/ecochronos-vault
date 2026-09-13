-- Spatial/temporal archive chunk index (PostGIS + checksum).
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS chunk_metadata (
    id              BIGSERIAL PRIMARY KEY,
    chunk_id        TEXT NOT NULL UNIQUE,
    storage_key     TEXT NOT NULL UNIQUE,
    uri             TEXT,
    dataset         TEXT,
    checksum        TEXT NOT NULL,
    checksum_alg    TEXT NOT NULL DEFAULT 'sha256',
    time_start      TIMESTAMPTZ NOT NULL,
    time_end        TIMESTAMPTZ NOT NULL,
    extent          geometry(Polygon, 4326) NOT NULL,
    size_bytes      BIGINT,
    content_type    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chunk_time_range CHECK (time_end >= time_start),
    CONSTRAINT chunk_checksum_sha256 CHECK (
        checksum_alg <> 'sha256'
        OR checksum ~ '^[0-9a-f]{64}$'
    )
);

CREATE INDEX IF NOT EXISTS chunk_metadata_extent_gix
    ON chunk_metadata USING GIST (extent);

CREATE INDEX IF NOT EXISTS chunk_metadata_time_idx
    ON chunk_metadata (time_start, time_end);

CREATE INDEX IF NOT EXISTS chunk_metadata_storage_key_prefix_idx
    ON chunk_metadata (storage_key text_pattern_ops);

CREATE INDEX IF NOT EXISTS chunk_metadata_dataset_idx
    ON chunk_metadata (dataset);
