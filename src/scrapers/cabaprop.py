from __future__ import annotations

import logging
import time
from typing import Dict, List

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


class CabapropScraper(BaseScraper):
    """Scraper para cabaprop.com.ar utilizando su API interna JSON."""

    portal_name: str = "cabaprop"
    API_URL = "https://cabaprop.com.ar/api/v1/properties/find-properties"
    BARRIOS_URL = "https://cabaprop.com.ar/api/v1/utils/barrios-flat"
    MAX_PAGES = 3

    def __init__(self):
        super().__init__()
        self._barrios_map: Dict[int, str] = {}

    def _load_barrios_map(self) -> None:
        """Carga el mapa de IDs de barrio a nombres desde la API de cabaprop."""
        if self._barrios_map:
            return
        try:
            r = self.session.get(self.BARRIOS_URL, timeout=15)
            if r.status_code == 200:
                data = r.json()
                for b in data:
                    val = b.get("value")
                    label = b.get("label")
                    if val is not None and label:
                        self._barrios_map[val] = label
                logger.info(f"[{self.portal_name}] Cargados {len(self._barrios_map)} barrios en memoria.")
        except Exception as e:
            logger.warning(f"[{self.portal_name}] No se pudo cargar mapa de barrios: {e}")

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
        Consulta la API de cabaprop para departamentos en alquiler en CABA.
        Paginación de hasta 3 páginas (offset=0, offset=1, offset=2).
        """
        self._load_barrios_map()
        all_listings: List[Listing] = []
        seen_ids = set()
        logger.info(f"--- Iniciando extracción para {self.portal_name.upper()} ---")

        # Ambientes a solicitar: [MIN_ROOMS, MIN_ROOMS + 1, MIN_ROOMS + 2]
        requested_ambiences = list(range(MIN_ROOMS, MIN_ROOMS + 3))

        for page in range(self.MAX_PAGES):
            try:
                url = f"{self.API_URL}?offset={page}&limit=20&orderBy=created_at&sort=desc"
                payload = {
                    "operationType": 2,  # Alquiler
                    "propertyType": 1,   # Departamento
                    "ambiences": requested_ambiences,
                }
                logger.info(f"[{self.portal_name}] POST API offset {page}")
                resp = self.session.post(url, json=payload, timeout=15)
                if resp.status_code not in (200, 201):
                    logger.warning(
                        f"[{self.portal_name}] API respondió con status {resp.status_code} en offset {page}"
                    )
                    break

                json_data = resp.json()
                items = json_data.get("result", [])
                if not items:
                    logger.info(f"[{self.portal_name}] No hay más resultados en offset {page}.")
                    break

                for it in items:
                    try:
                        property_id = str(it.get("_id") or "")
                        if not property_id or property_id in seen_ids:
                            continue

                        # 1. Moneda: 2 = ARS, 1 = USD
                        price_info = it.get("price") or {}
                        currency_id = price_info.get("currency")
                        if currency_id != 2:
                            continue

                        # 2. Ubicación: Extraer nombres de barrios a partir de los IDs
                        loc_info = it.get("location") or {}
                        barrio_ids = loc_info.get("barrios", [])
                        barrio_names = [self._barrios_map.get(bid, "") for bid in barrio_ids if bid in self._barrios_map]
                        street = loc_info.get("street") or ""
                        number = loc_info.get("number") or ""
                        address = f"{street} {number}".strip()

                        combined_loc = " ".join(barrio_names) + f" {address}"
                        matched_nb = self._match_neighborhood(combined_loc)
                        if not matched_nb:
                            # Intentar con el título y permalink
                            title_raw = it.get("title") or ""
                            permalink_raw = it.get("permalink") or ""
                            matched_nb = self._match_neighborhood(f"{title_raw} {permalink_raw}")

                        if not matched_nb:
                            continue

                        # 3. Precios y Expensas
                        price = float(price_info.get("total") or 0.0)
                        if price < 150000:
                            continue

                        expenses = float(price_info.get("expenses") or 0.0)
                        total_cost = price + expenses
                        if total_cost > MAX_TOTAL_BUDGET_ARS:
                            continue

                        # Descartar si el alquiler está a menos de 50.000 del tope sin expensas especificadas
                        if expenses <= 0.0 and price >= (MAX_TOTAL_BUDGET_ARS - 150000):
                            continue

                        # 4. Ambientes
                        chars = it.get("characteristics") or {}
                        rooms = int(chars.get("ambience") or 0)
                        if 0 < rooms < MIN_ROOMS:
                            continue

                        seen_ids.add(property_id)

                        # 5. Superficie y Balcón
                        surf = it.get("surface") or {}
                        m2 = float(surf.get("totalSurface") or surf.get("coveredSurface") or 0.0)

                        amb_types = it.get("ambiences_types") or {}
                        has_balcony = bool(amb_types.get("balcon"))
                        if not has_balcony:
                            desc = it.get("description") or ""
                            title_str = it.get("title") or ""
                            has_balcony = self.detect_balcony(desc) or self.detect_balcony(title_str)

                        permalink = (it.get("permalink") or "").strip()
                        if not permalink:
                            permalink = f"https://cabaprop.com.ar/propiedad/{property_id}"

                        # Imagen
                        images = it.get("images") or []
                        thumbnail_url = images[0].get("url", "") if images else ""

                        title = (it.get("title") or f"Departamento en alquiler en {matched_nb}").strip()

                        listing = Listing(
                            id=f"cabaprop_{property_id}",
                            portal="cabaprop",
                            portal_id=property_id,
                            url=permalink,
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
                logger.error(f"[{self.portal_name}] Error en offset {page}: {page_err}")
                break

        logger.info(f"[{self.portal_name}] Total extraídos en barrios objetivo: {len(all_listings)}")
        return all_listings
