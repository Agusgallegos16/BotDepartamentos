from __future__ import annotations

import logging
import re
from typing import List, Optional
from selectolax.lexbor import LexborHTMLParser

from src.config import (
    MAX_TOTAL_BUDGET_ARS,
    MIN_ROOMS,
    NEIGHBORHOOD_SLUGS,
    Listing,
)
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class ZonapropScraper(BaseScraper):
    """Scraper for Zonaprop utilizing curl_cffi TLS impersonation."""

    portal_name: str = "zonaprop"
    BASE_URL = "https://www.zonaprop.com.ar"

    def _build_url(self, neighborhood_slug: str) -> str:
        max_budget = int(MAX_TOTAL_BUDGET_ARS)
        return f"{self.BASE_URL}/departamentos-alquiler-{neighborhood_slug}-hasta-{max_budget}-pesos.html"

    def scrape_neighborhood(self, neighborhood: str) -> List[Listing]:
        slug_info = NEIGHBORHOOD_SLUGS.get(neighborhood)
        if not slug_info:
            logger.warning(f"[{self.portal_name}] Barrio no mapeado: '{neighborhood}'")
            return []

        url = self._build_url(slug_info["zonaprop"])
        html_content = self.fetch_html(url)
        if not html_content:
            return []

        parser = LexborHTMLParser(html_content)
        cards = parser.css("div[data-posting-type]")
        if not cards:
            cards = parser.css(".postingCard")

        listings: List[Listing] = []

        for card in cards:
            try:
                portal_id = card.attributes.get("data-id") or ""
                posting_path = card.attributes.get("data-to-posting") or ""

                if not portal_id and posting_path:
                    id_match = re.search(r"-(\d+)\.html", posting_path)
                    if id_match:
                        portal_id = id_match.group(1)

                if not portal_id or not posting_path:
                    continue

                full_url = (
                    f"{self.BASE_URL}{posting_path}"
                    if posting_path.startswith("/")
                    else posting_path
                )
                # Limpiar query params de tracking
                if "?" in full_url:
                    full_url = full_url.split("?")[0]

                # Precio de alquiler
                price_elem = card.css_first(
                    "[class*='postingPrices-module__price'], [data-qa='POSTING_CARD_PRICE']"
                )
                raw_price = price_elem.text(strip=True) if price_elem else ""
                price_ars = self.parse_ars_number(raw_price)

                # Si el precio es 0 o está en USD, saltar
                if price_ars <= 0:
                    continue

                # Expensas
                exp_elem = card.css_first(
                    "[class*='postingPrices-module__expenses'], [data-qa='expensas']"
                )
                raw_exp = exp_elem.text(strip=True) if exp_elem else ""
                expenses_ars = self.parse_ars_number(raw_exp)

                # Título y dirección
                title = ""
                for h in card.css("h2, h3, [class*='postingCard-module__title'], a"):
                    txt = h.text(strip=True)
                    if (
                        txt
                        and not txt.startswith("$")
                        and not txt.startswith("USD")
                        and not txt.startswith("U$S")
                        and len(txt) > 5
                    ):
                        title = txt
                        break

                loc_elem = card.css_first(
                    "[class*='postingLocations-module__location-block'], [data-qa='POSTING_CARD_LOCATION']"
                )
                address = loc_elem.text(strip=True) if loc_elem else ""
                if not title:
                    title = f"Departamento en {slug_info['name']} ({address})"

                # Características (ambientes, m2)
                feature_spans = card.css(
                    "[class*='postingMainFeatures-module__posting-main-features-span']"
                )
                feature_texts = [f.text(strip=True) for f in feature_spans]
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

                # Si no se extrajo de features, buscar en texto completo
                if rooms == 0:
                    rooms = self.extract_rooms(all_text)

                # Filtrar si ambientes conocidos son menores a MIN_ROOMS
                if 0 < rooms < MIN_ROOMS:
                    continue

                has_balcony = self.detect_balcony(all_text)

                # Imagen de portada
                img_elem = card.css_first("img")
                img_url = ""
                if img_elem:
                    img_url = (
                        img_elem.attributes.get("src")
                        or img_elem.attributes.get("data-src")
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
                listings.append(listing)

            except Exception as e:
                logger.debug(f"[{self.portal_name}] Error parseando tarjeta individual: {e}")
                continue

        return listings
