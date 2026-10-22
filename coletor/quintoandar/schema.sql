-- QuintoAndar pipeline schema (PostgreSQL 16)
-- kind = 'sale' | 'rent'; a property can exist as both -> composite PK (id, kind).

CREATE TABLE IF NOT EXISTS listings (
    id                BIGINT NOT NULL,             -- quintoandar listing id
    kind              TEXT NOT NULL DEFAULT 'sale',
    link              TEXT NOT NULL,
    type              TEXT,
    description       TEXT,
    address           TEXT,
    neighborhood      TEXT,
    neighborhood_norm TEXT,       -- accent/case/space-normalized
    region            TEXT,
    region_norm       TEXT,
    city              TEXT,
    lat               DOUBLE PRECISION,
    lng               DOUBLE PRECISION,
    area              NUMERIC,
    bedrooms          INT,
    bathrooms         INT,
    suites            INT,
    parking           INT,
    condominium       NUMERIC,     -- iptuPlusCondominium
    price             NUMERIC,     -- salePrice (sale) or monthly rent (rent)
    price_m2          NUMERIC,
    total_cost        NUMERIC,
    rent              NUMERIC,
    yield             NUMERIC,
    is_furnished      BOOLEAN,
    is_primary_market BOOLEAN,
    for_rent          BOOLEAN,
    for_sale          BOOLEAN,
    accepts_pets      BOOLEAN,
    amenities         TEXT,
    installations     TEXT,
    cover_image       TEXT,
    status            TEXT NOT NULL DEFAULT 'active',  -- active | off_market
    first_seen        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen         TIMESTAMPTZ NOT NULL DEFAULT now(),
    price_changed_at  TIMESTAMPTZ,
    removed_at        TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, kind)
);

CREATE INDEX IF NOT EXISTS idx_listings_kind         ON listings(kind, status);
CREATE INDEX IF NOT EXISTS idx_listings_neighborhood ON listings(neighborhood);
CREATE INDEX IF NOT EXISTS idx_listings_nbnorm       ON listings(neighborhood_norm);
CREATE INDEX IF NOT EXISTS idx_listings_region       ON listings(region);
CREATE INDEX IF NOT EXISTS idx_listings_regnorm      ON listings(region_norm);
CREATE INDEX IF NOT EXISTS idx_listings_price        ON listings(price);
CREATE INDEX IF NOT EXISTS idx_listings_last_seen    ON listings(last_seen);

CREATE TABLE IF NOT EXISTS price_history (
    id          BIGSERIAL PRIMARY KEY,
    listing_id  BIGINT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'sale',
    price       NUMERIC NOT NULL,
    price_m2    NUMERIC,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (listing_id, kind) REFERENCES listings(id, kind) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_price_history_listing ON price_history(listing_id, kind, observed_at);

CREATE TABLE IF NOT EXISTS status_history (
    id          BIGSERIAL PRIMARY KEY,
    listing_id  BIGINT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'sale',
    status      TEXT NOT NULL,
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (listing_id, kind) REFERENCES listings(id, kind) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_status_history_listing ON status_history(listing_id, kind, changed_at);

CREATE TABLE IF NOT EXISTS hidden (
    listing_id BIGINT NOT NULL,
    kind       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (listing_id, kind),
    FOREIGN KEY (listing_id, kind) REFERENCES listings(id, kind) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS saved_searches (
    id         BIGSERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'sale',
    params     JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id            BIGSERIAL PRIMARY KEY,
    kind          TEXT NOT NULL DEFAULT 'sale',
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    cells         INT,
    requests      INT,
    seen          INT,
    new_listings  INT,
    price_changes INT,
    reactivated   INT,
    off_market    INT,
    status        TEXT NOT NULL DEFAULT 'running',  -- running | ok | error
    error         TEXT
);

-- Enriquecimento sob demanda (quintoandar/enrich.py): ficha completa + avaliação do QuintoAndar
CREATE TABLE IF NOT EXISTS enrichment (
    listing_id         BIGINT NOT NULL,
    kind               TEXT NOT NULL,
    fetched_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    floor_min          INT,
    floor_max          INT,
    doorman            TEXT,
    near_subway        BOOLEAN,
    morning_sun        BOOLEAN,
    afternoon_sun      BOOLEAN,
    clear_view         BOOLEAN,
    quiet_street       BOOLEAN,
    balcony            BOOLEAN,
    elevator           BOOLEAN,
    pool               BOOLEAN,
    gym                BOOLEAN,
    construction_year  INT,
    condo_name         TEXT,
    zip                TEXT,
    first_published_at TIMESTAMPTZ,
    custom_offer       BOOLEAN,
    direct_offer       BOOLEAN,
    visit_status       TEXT,
    is_reserved        BOOLEAN,
    iptu               NUMERIC,
    condo_price        NUMERIC,
    photos             JSONB,
    features           JSONB,
    price_state        TEXT,       -- BELOW_PRICED | WELL_PRICED | OVER_PRICED
    suggested_price    NUMERIC,
    p10                NUMERIC,
    p30                NUMERIC,
    p70                NUMERIC,
    p90                NUMERIC,
    faster_price       NUMERIC,    -- preço sugerido para fechar rápido
    certainty          TEXT,
    similares          JSONB,      -- resposta de similar-houses (no ar + saíram do ar + resumo)
    error              TEXT,
    PRIMARY KEY (listing_id, kind),
    FOREIGN KEY (listing_id, kind) REFERENCES listings(id, kind) ON DELETE CASCADE
);

-- Vigia (quintoandar/watch.py): anúncios novos já avisados por pesquisa salva
CREATE TABLE IF NOT EXISTS watch_alerts (
    listing_id BIGINT NOT NULL,
    kind       TEXT NOT NULL,
    search_id  BIGINT NOT NULL,
    alerted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (listing_id, kind, search_id)
);
