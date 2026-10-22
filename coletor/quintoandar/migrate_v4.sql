-- normalized location columns (accent/case/space-insensitive filtering)
ALTER TABLE listings ADD COLUMN IF NOT EXISTS neighborhood_norm TEXT;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS region_norm       TEXT;
CREATE INDEX IF NOT EXISTS idx_listings_nbnorm  ON listings(neighborhood_norm);
CREATE INDEX IF NOT EXISTS idx_listings_regnorm ON listings(region_norm);

-- disliked/hidden listings (to filter out)
CREATE TABLE IF NOT EXISTS hidden (
    listing_id BIGINT NOT NULL,
    kind       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (listing_id, kind),
    FOREIGN KEY (listing_id, kind) REFERENCES listings(id, kind) ON DELETE CASCADE
);
ALTER TABLE hidden OWNER TO quintoandar;

-- saved filter combinations
CREATE TABLE IF NOT EXISTS saved_searches (
    id         BIGSERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'sale',
    params     JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE saved_searches OWNER TO quintoandar;
ALTER SEQUENCE saved_searches_id_seq OWNER TO quintoandar;
