CREATE TABLE IF NOT EXISTS favorites (
    listing_id          BIGINT NOT NULL,
    kind                TEXT NOT NULL,
    note                TEXT,
    target_price        NUMERIC,       -- optional: alert when price <= this
    price_at_fav        NUMERIC,       -- price when favorited (baseline)
    last_notified_price NUMERIC,       -- lowest price already alerted (dedup)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (listing_id, kind),
    FOREIGN KEY (listing_id, kind) REFERENCES listings(id, kind) ON DELETE CASCADE
);
