BEGIN;

ALTER TABLE listings       ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'sale';
ALTER TABLE listings       ADD COLUMN IF NOT EXISTS accepts_pets BOOLEAN;
ALTER TABLE price_history  ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'sale';
ALTER TABLE status_history ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'sale';
ALTER TABLE scrape_runs    ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'sale';

-- rebuild listings PK as composite (id, kind) so the same property can exist
-- both for sale and for rent without colliding
ALTER TABLE price_history  DROP CONSTRAINT IF EXISTS price_history_listing_id_fkey;
ALTER TABLE status_history DROP CONSTRAINT IF EXISTS status_history_listing_id_fkey;
ALTER TABLE listings DROP CONSTRAINT IF EXISTS listings_pkey;
ALTER TABLE listings ADD CONSTRAINT listings_pkey PRIMARY KEY (id, kind);

ALTER TABLE price_history  ADD CONSTRAINT price_history_listing_fkey
  FOREIGN KEY (listing_id, kind) REFERENCES listings(id, kind) ON DELETE CASCADE;
ALTER TABLE status_history ADD CONSTRAINT status_history_listing_fkey
  FOREIGN KEY (listing_id, kind) REFERENCES listings(id, kind) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_listings_kind   ON listings(kind, status);
CREATE INDEX IF NOT EXISTS idx_listings_region ON listings(region);

COMMIT;
