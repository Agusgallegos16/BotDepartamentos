from __future__ import annotations

import logging
import time
from typing import List

from src.config import (
    DELAY_BETWEEN_REQUESTS,
    MAX_TOTAL_BUDGET_ARS,
    MIN_ROOMS,
    NEIGHBORHOOD_SLUGS,
    TARGET_NEIGHBORHOODS,
    Listing,
)
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class MobScraper(BaseScraper):
    """Scraper para mob.ar utilizando su API interna JSON."""

    portal_name: str = "mob"
    API_URL = "https://www.mob.ar/api/properties/search"
    MAX_PAGES = 3

    def _match_neighborhood(self, text: str) -> str:
        """Determina a qué barrio objetivo pertenece el aviso a partir del texto."""
        text_lower = text.lower()
        aliases = {
            "palermo": [
                "palermo", "botánico", "botanico", "las cañitas", "cañitas",
                "palermo soho", "palermo hollywood", "palermo chico", "palermo viejo"
            ],
            "caballito": [
                "caballito", "parque centenario", "primera junta", "caballito norte", "caballito sur"
            ],
            "belgrano": [
                "belgrano", "barrancas de belgrano", "belgrano r", "belgrano c", "belgrano chico"
            ],
            "villa-urquiza": ["villa urquiza", "urquiza r", "urquiza"],
            "villa-del-parque": ["villa del parque"],
            "chacarita": ["chacarita"],
            "recoleta": ["recoleta", "barrio norte"],
            "colegiales": ["colegiales"],
            "villa-crespo": ["villa crespo"],
        }

        for key in TARGET_NEIGHBORHOODS:
            nb_info = NEIGHBORHOOD_SLUGS.get(key)
            canonical_name = nb_info["name"] if nb_info else key.replace("-", " ").title()
            variations = aliases.get(key, [key.replace("-", " "), canonical_name.lower()])
            for var in variations:
                if var in text_lower:
                    return canonical_name
        return ""

    def scrape_neighborhood(self, neighborhood: str) -> List[Listing]:
        """Interfaz de BaseScraper: filtra resultados globales para el barrio pedido."""
        all_results = self.scrape_all()
        target_name = NEIGHBORHOOD_SLUGS.get(neighborhood, {}).get("name", "").lower()
        return [l for l in all_results if l.neighborhood.lower() == target_name]

    def scrape_all(self) -> List[Listing]:
        """
        Consulta la API de mob.ar para CABA con límite de 3 páginas
        y aplica las reglas de negocio unificadas.
        """
        all_listings: List[Listing] = []
        seen_ids = set()
        logger.info(f"--- Iniciando extracción para {self.portal_name.upper()} ---")

        for page in range(1, self.MAX_PAGES + 1):
            try:
                params = {
                    "operation": "alquiler",
                    "minAmbientes": MIN_ROOMS,
                    "state": "capital-federal",
                    "sort": "recent",
                    "maxPrice": int(MAX_TOTAL_BUDGET_ARS),
                    "page": page,
                    "limit": 20,
                }
                logger.info(f"[{self.portal_name}] GET API página {page}")
                resp = self.session.get(self.API_URL, params=params, timeout=15)
                if resp.status_code != 200:
                    logger.warning(
                        f"[{self.portal_name}] API respondió con status {resp.status_code} en página {page}"
                    )
                    break

                json_data = resp.json()
                items = json_data.get("data", [])
                if not items:
                    logger.info(f"[{self.portal_name}] No hay más resultados en página {page}.")
                    break

                for it in items:
                    try:
                        property_id = str(it.get("property_id") or "")
                        if not property_id or property_id in seen_ids:
                            continue

                        # 1. Moneda: Solo ARS
                        currency = (it.get("currency") or "").strip().upper()
                        if currency != "ARS":
                            continue

                        # 2. Ubicación: Validar si pertenece a los barrios objetivo
                        loc_name = it.get("location_name") or ""
                        parent_loc = it.get("parent_location_name") or ""
                        combined_loc = f"{loc_name} {parent_loc}".strip()
                        matched_nb = self._match_neighborhood(combined_loc)
                        if not matched_nb:
                            continue

                        # 3. Precios y Expensas
                        price = float(it.get("price") or 0.0)
                        if price < 150000:
                            continue

                        expenses = float(it.get("expenses") or 0.0)
                        total_cost = price + expenses
                        if total_cost > MAX_TOTAL_BUDGET_ARS:
                            continue

                        # Descartar si el alquiler está a menos de 50.000 del tope sin expensas especificadas
                        if expenses <= 0.0 and price >= (MAX_TOTAL_BUDGET_ARS - 150000):
                            continue

                        # 4. Ambientes
                        rooms = int(it.get("room_amount") or 0)
                        if 0 < rooms < MIN_ROOMS:
                            continue

                        seen_ids.add(property_id)

                        # 5. Superficie y Balcón
                        m2 = float(it.get("total_surface") or 0.0)
                        slug = it.get("slug") or f"propiedad-{property_id}"
                        url = f"https://www.mob.ar/propiedad/{slug}"
                        address = (it.get("address") or "").strip()
                        desc = it.get("description") or ""

                        tags = []
                        for tag_key in ("tag_names_type_1", "tag_names_type_2", "tag_names_type_3"):
                            tag_list = it.get(tag_key)
                            if isinstance(tag_list, list):
                                tags.extend(tag_list)

                        has_balcony = (
                            any(self.detect_balcony(str(t)) for t in tags)
                            or self.detect_balcony(desc)
                            or self.detect_balcony(slug)
                            or self.detect_balcony(address)
                        )

                        thumbnail_url = (it.get("cover_photo_url") or "").strip()
                        title = f"Departamento en alquiler en {matched_nb} - {address}".strip(" -")

                        listing = Listing(
                            id=f"mob_{property_id}",
                            portal="mob",
                            portal_id=property_id,
                            url=url,
                            title=title,
                            neighborhood=matched_nb,
                            address=address,
                            price_ars=price,
                            expenses_ars=expenses,
                            total_cost_ars=total_cost,
                            rooms=rooms,
                            m2=m2,
                            has_balcony=has_balcony,
                            thumbnail_url=thumbnail_url,
                        )
                        all_listings.append(listing)
                    except Exception as item_err:
                        logger.debug(f"[{self.portal_name}] Error procesando ítem {it}: {item_err}")
                        continue

                time.sleep(DELAY_BETWEEN_REQUESTS)

            except Exception as page_err:
                logger.error(f"[{self.portal_name}] Error en página {page}: {page_err}")
                break

        logger.info(f"[{self.portal_name}] Total extraídos en barrios objetivo: {len(all_listings)}")
        return all_listings
