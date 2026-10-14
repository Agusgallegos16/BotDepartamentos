from __future__ import annotations

import logging
import re
from typing import List
from selectolax.lexbor import LexborHTMLParser

from src.config import (
    MAX_TOTAL_BUDGET_ARS,
    MIN_ROOMS,
    NEIGHBORHOOD_SLUGS,
    Listing,
)
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class ArgenpropScraper(BaseScraper):
    """Scraper for Argenprop with robust fail-soft against AWS WAF challenges."""

    portal_name: str = "argenprop"
    BASE_URL = "https://www.argenprop.com"

    def _build_url(self, neighborhood_slug: str) -> str:
        max_budget = int(MAX_TOTAL_BUDGET_ARS)
        if MIN_ROOMS <= 1:
            room_slug = "1-ambiente-o-2-ambientes-o-3-ambientes"
        elif MIN_ROOMS == 2:
            room_slug = "2-ambientes-o-3-ambientes"
        elif MIN_ROOMS == 3:
            room_slug = "3-ambientes-o-4-ambientes"
        else:
            room_slug = f"{MIN_ROOMS}-ambientes"

        return (
            f"{self.BASE_URL}/departamentos/alquiler/{neighborhood_slug}/"
            f"{room_slug}?hasta-{max_budget}"
        )

    def scrape_neighborhood(self, neighborhood: str) -> List[Listing]:
        slug_info = NEIGHBORHOOD_SLUGS.get(neighborhood)
        if not slug_info:
            logger.warning(f"[{self.portal_name}] Barrio no mapeado: '{neighborhood}'")
            return []

        base_url = self._build_url(slug_info["argenprop"])
        listings: List[Listing] = []
        seen_ids = set()

        for page in range(1, 4):
            page_url = base_url if page == 1 else f"{base_url}&pagina={page}"
            html_content = self.fetch_html(page_url)
            if not html_content:
                break

            parser = LexborHTMLParser(html_content)
            cards = parser.css("div.listing__item")
            if not cards:
                logger.debug(
                    f"[{self.portal_name}] No se encontraron tarjetas en {page_url} (posible desafío WAF o sin resultados)."
                )
                break

            new_on_page = 0
            for card in cards:
                try:
                    link_elem = card.css_first("a.card, a")
                    if not link_elem:
                        continue

                    href = link_elem.attributes.get("href") or ""
                    if not href:
                        continue

                    full_url = (
                        f"{self.BASE_URL}{href}" if href.startswith("/") else href
                    )

                    # Extraer ID numérico del link (ej: --20491509)
                    id_match = re.search(r"--(\d+)", href)
                    portal_id = (
                        id_match.group(1)
                        if id_match
                        else card.attributes.get("data-item-id", str(hash(href)))
                    )

                    if portal_id in seen_ids:
                        continue

                    # Precio
                    price_elem = card.css_first("p.card__price")
                    raw_price = price_elem.text(strip=True) if price_elem else ""
                    price_ars = self.parse_ars_number(raw_price)

                    if price_ars <= 0:
                        continue

                    # Expensas
                    exp_elem = card.css_first("span.card__expenses, .card__expenses")
                    raw_exp = exp_elem.text(strip=True) if exp_elem else ""
                    expenses_ars = self.parse_ars_number(raw_exp)

                    # Título y dirección
                    title_elem = card.css_first("h2.card__title, p.card__title--primary")
                    title = title_elem.text(strip=True) if title_elem else "Departamento"

                    addr_elem = card.css_first("p.card__address, .card__address")
                    address = addr_elem.text(strip=True) if addr_elem else slug_info["name"]

                    # Características (ambientes, m2)
                    feature_items = card.css("ul.card__main-features li")
                    feature_texts = [f.text(strip=True) for f in feature_items]
                    all_text = " ".join([title, address] + feature_texts)

                    rooms = 0
                    m2 = 0.0

                    for feat in feature_texts:
                        feat_lower = feat.lower()
                        if "amb" in feat_lower:
                            rooms = self.extract_rooms(feat)
                        elif "m²" in feat_lower or "m2" in feat_lower:
                            m2_match = re.search(r"(\d+)", feat)
                            if m2_match:
                                m2 = float(m2_match.group(1))

                    if rooms == 0:
                        rooms = self.extract_rooms(all_text)

                    if 0 < rooms < MIN_ROOMS:
                        continue

                    has_balcony = self.detect_balcony(all_text)

                    # Imagen
                    img_elem = card.css_first("img")
                    img_url = ""
                    if img_elem:
                        img_url = (
                            img_elem.attributes.get("data-src")
                            or img_elem.attributes.get("src")
                            or ""
                        )

                    listing = Listing(
                        id=f"{self.portal_name}_{portal_id}",
                        portal=self.portal_name,
                        portal_id=portal_id,
                        url=full_url,
                        title=title,
                        neighborhood=slug_info["name"],
                        address=address,
                        price_ars=price_ars,
                        expenses_ars=expenses_ars,
                        rooms=rooms,
                        m2=m2,
                        has_balcony=has_balcony,
                        thumbnail_url=img_url,
                    )
                    seen_ids.add(portal_id)
                    listings.append(listing)
                    new_on_page += 1

                except Exception as e:
                    logger.debug(f"[{self.portal_name}] Error en tarjeta: {e}")
                    continue

            # Si no hubo avisos nuevos o la página tuvo menos de 20 tarjetas, cortar
            if new_on_page == 0 or len(cards) < 20:
                break

        return listings
