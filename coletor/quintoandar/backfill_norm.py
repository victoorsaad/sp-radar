"""One-time backfill of neighborhood_norm / region_norm on existing rows.

Run once after migrate_v4.sql. Idempotent (safe to re-run). The pipeline keeps
these columns up to date on every crawl going forward.

Run:  python -m quintoandar.backfill_norm
"""
import logging

from . import db
from .textnorm import norm_name

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
LOG = logging.getLogger("quintoandar.backfill")


def backfill():
    with db.connect() as conn:
        for col, ncol in (("neighborhood", "neighborhood_norm"), ("region", "region_norm")):
            vals = conn.execute(
                f"SELECT DISTINCT {col} FROM listings WHERE {col} IS NOT NULL"
            ).fetchall()
            n = 0
            for (raw,) in vals:
                conn.execute(
                    f"UPDATE listings SET {ncol}=%s WHERE {col}=%s",
                    (norm_name(raw), raw))
                n += 1
            conn.commit()
            LOG.info("%s: normalized %s distinct values", col, n)


if __name__ == "__main__":
    backfill()
    print("backfill done")
