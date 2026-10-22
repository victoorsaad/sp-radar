"""Discord notifications for price drops on favorited listings.

Called at the end of each pipeline run. A favorite is alerted when the current
price drops below its baseline (the lowest price already alerted, or the price
at the moment it was favorited). Each new low is alerted once.

Configure by putting DISCORD_WEBHOOK_URL in config/.env. If unset, this no-ops.
"""
import logging
import os

import requests

from . import db  # noqa: F401  (ensures .env is loaded via db module)

LOG = logging.getLogger("quintoandar.notifier")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

GREEN = 0x3FB950
KIND_LABEL = {"sale": "venda", "rent": "aluguel"}


def _fmt(n):
    return f"R$ {n:,.0f}".replace(",", ".") if n is not None else "—"


def _post(payload):
    r = requests.post(WEBHOOK, json=payload, timeout=20)
    if r.status_code not in (200, 204):
        LOG.warning("discord webhook HTTP %s: %s", r.status_code, r.text[:200])
        return False
    return True


def check_and_notify(kind):
    """Find favorites whose price improved and alert. Returns count alerted."""
    if not WEBHOOK:
        LOG.info("DISCORD_WEBHOOK_URL not set; skipping notifications")
        return 0

    suffix = "/mês" if kind == "rent" else ""
    with db.connect() as conn:
        rows = conn.execute("""
            SELECT f.listing_id, f.price_at_fav, f.last_notified_price, f.target_price,
                   f.note, l.price, l.total_cost, l.neighborhood, l.region, l.address,
                   l.area, l.bedrooms, l.link
            FROM favorites f
            JOIN listings l ON l.id = f.listing_id AND l.kind = f.kind
            WHERE f.kind = %s AND l.status = 'active' AND l.price IS NOT NULL
        """, (kind,)).fetchall()

        alerts = []  # (listing_id, embed, new_price)
        for r in rows:
            (lid, price_at_fav, last_notified, target, note, price, total_cost,
             neigh, region, address, area, bedrooms, link) = r
            price = float(price)
            baseline = last_notified if last_notified is not None else price_at_fav
            if baseline is None:
                continue
            baseline = float(baseline)
            if price >= baseline:
                continue  # no improvement below the last alerted low

            pct = round((price - float(price_at_fav)) / float(price_at_fav) * 100, 1) \
                if price_at_fav else None
            hit_target = target is not None and price <= float(target)
            title = f"{neigh or ''}{' · ' + region if region else ''}".strip(" ·") or "Imóvel"
            desc_bits = []
            if address:
                desc_bits.append(address)
            meta = " · ".join(filter(None, [
                f"{area:.0f} m²" if area else None,
                f"{bedrooms}q" if bedrooms else None,
                f"total {_fmt(total_cost)}{suffix}" if total_cost else None,
            ]))
            if meta:
                desc_bits.append(meta)
            if note:
                desc_bits.append(f"📝 {note}")
            if hit_target:
                desc_bits.append(f"🎯 atingiu seu alvo de {_fmt(target)}{suffix}!")

            embed = {
                "title": f"📉 {title}",
                "url": link,
                "color": GREEN,
                "description": "\n".join(desc_bits),
                "fields": [
                    {"name": "Baixou de", "value": f"{_fmt(baseline)}{suffix}", "inline": True},
                    {"name": "para", "value": f"{_fmt(price)}{suffix}", "inline": True},
                    {"name": "vs. favoritado",
                     "value": (f"{pct:+.1f}%" if pct is not None else "—"), "inline": True},
                ],
            }
            alerts.append((lid, embed, price))

        if not alerts:
            LOG.info("[%s] no favorite price drops to notify", kind)
            return 0

        # Discord allows up to 10 embeds per message
        sent = 0
        for i in range(0, len(alerts), 10):
            chunk = alerts[i:i + 10]
            content = (f"💚 **{len(chunk)} favorito(s) de {KIND_LABEL.get(kind, kind)} "
                       f"baixaram de preço**")
            if _post({"content": content, "embeds": [e for _, e, _ in chunk]}):
                for lid, _, new_price in chunk:
                    conn.execute(
                        "UPDATE favorites SET last_notified_price=%s "
                        "WHERE listing_id=%s AND kind=%s", (new_price, lid, kind))
                    sent += 1
        conn.commit()
        LOG.info("[%s] notified %s favorite price drops", kind, sent)
        return sent


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    check_and_notify(sys.argv[1] if len(sys.argv) > 1 else "rent")
