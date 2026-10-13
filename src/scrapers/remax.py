from __future__ import annotations

import logging
from typing import List
from src.config import (
    MAX_TOTAL_BUDGET_ARS,
    MIN_ROOMS,
    NEIGHBORHOOD_SLUGS,
    Listing,
)
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class RemaxScraper(BaseScraper):
    """Scraper for Remax Argentina utilizing their direct REST API."""

    portal_name: str = "remax"
    API_URL = "https://api-ar.redremax.com/remaxweb-ar/api/listings/findAllWithEntrepreneurships"
    IMAGE_CDN_PREFIX = "https://d1acdg20u0pmxj.cloudfront.net/860x440"
    WEB_BASE_URL = "https://www.remax.com.ar/listings"

    def _match_neighborhood(self, geo_label: str) -> str:
        """Determines if the listing's geoLabel matches any configured neighborhood."""
        geo_lower = geo_label.lower()
        for key, info in NEIGHBORHOOD_SLUGS.items():
            # Comparar tanto con el slug como con el nombre en español (ej: "villa del parque", "villa-del-parque")
            normalized_name = info["name"].lower()
            normalized_slug = key.replace("-", " ")
            if normalized_name in geo_lower or normalized_slug in geo_lower:
                return info["name"]
        return ""

    def scrape_neighborhood(self, neighborhood: str) -> List[Listing]:
        """Implemented for BaseScraper interface. Re-delegates to scrape_all."""
        all_results = self.scrape_all()
        target_name = NEIGHBORHOOD_SLUGS.get(neighborhood, {}).get("name", "").lower()
        return [l for l in all_results if l.neighborhood.lower() == target_name]

    def scrape_all(self) -> List[Listing]:
        """
        Queries the Remax API for recent rentals in CABA.
        As the API allows fetching bulk recent listings, 1 or 2 requests
        cover all target neighborhoods instantly and efficiently.
        """
        all_listings: List[Listing] = []
        logger.info(f"--- Iniciando extracción para {self.portal_name.upper()} ---")

        # Consultar las primeras 2 páginas (hasta 100 publicaciones recientes de alquiler)
        for page in range(2):
            try:
                params = {
                    "page": page,
                    "pageSize": 50,
                    "sort": "-createdAt",
                    "in": "operationId:2",  # 2 = alquiler tradicional
                }
                logger.info(f"[{self.portal_name}] GET API page {page}")
                resp = self.session.get(self.API_URL, params=params, timeout=15)
                if resp.status_code != 200:
                    logger.warning(
                        f"[{self.portal_name}] API respondió con status {resp.status_code}"
                    )
                    break

                json_data = resp.json()
                items = json_data.get("data", {}).get("data", [])
                if not items:
                    break

                for it in items:
                    try:
                        # 1. Moneda: Solo ARS
                        curr = it.get("currency", {}).get("value", "")
                        if curr != "ARS":
                            continue

                        # 2. Ubicación: Validar si pertenece a CABA y a los barrios objetivo
                        geo_label = it.get("geoLabel", "") or ""
                        matched_nb = self._match_neighborhood(geo_label)
                        if not matched_nb:
                            continue

                        # 3. Precios y Expensas
                        price = float(it.get("price") or 0.0)
                        if price <= 0:
                            continue

                        expenses = float(it.get("expensesPrice") or 0.0)
                        total_cost = price + expenses
                        if total_cost > MAX_TOTAL_BUDGET_ARS:
                            continue

                        # 4. Ambientes
                        rooms = int(it.get("totalRooms") or 0)
                        if 0 < rooms < MIN_ROOMS:
                            continue

                        # 5. Metros cuadrados y Balcón
                        m2 = float(
                            it.get("dimensionTotalBuilt")
                            or it.get("dimensionCovered")
                            or 0.0
                        )
                        title = (it.get("title") or "Departamento en Alquiler").strip()
                        slug = it.get("slug") or ""
                        has_balcony = self.detect_balcony(f"{title} {slug}")

                        # 6. ID y Enlace
                        listing_id = str(it.get("id"))
                        full_url = (
                            f"{self.WEB_BASE_URL}/{slug}"
                            if slug
                            else f"{self.WEB_BASE_URL}/{listing_id}"
                        )
                        address = it.get("displayAddress") or matched_nb

                        # 7. Foto de portada
                        photos = it.get("photos", [])
                        thumb_url = ""
                        if photos and isinstance(photos, list):
                            raw_val = photos[0].get("rawValue", "")
                            if raw_val:
                                thumb_url = f"{self.IMAGE_CDN_PREFIX}/{raw_val}"

                        listing = Listing(
                            id=f"{self.portal_name}_{listing_id}",
                            portal=self.portal_name,
                            portal_id=listing_id,
                            url=full_url,
                            title=title,
                            neighborhood=matched_nb,
                            address=address,
                            price_ars=price,
                            expenses_ars=expenses,
                            total_cost_ars=total_cost,
                            rooms=rooms,
                            m2=m2,
                            has_balcony=has_balcony,
                            thumbnail_url=thumb_url,
                        )
                        all_listings.append(listing)

                    except Exception as item_err:
                        logger.debug(
                            f"[{self.portal_name}] Error parseando item individual: {item_err}"
                        )
                        continue

            except Exception as e:
                logger.error(f"[{self.portal_name}] Error en request API página {page}: {e}")
                break

        logger.info(
            f"[{self.portal_name}] Total extraídos en barrios objetivo: {len(all_listings)}"
        )
        return all_listings
