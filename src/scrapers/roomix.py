from __future__ import annotations

import logging
import re
import time
from typing import List

from curl_cffi import requests
from selectolax.lexbor import LexborHTMLParser

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


class RoomixScraper(BaseScraper):
    """Scraper para roomix.ai (Next.js SSR)."""

    portal_name: str = "roomix"
    BASE_URL = "https://roomix.ai"
    MAX_PAGES = 7

    def __init__(self):
        super().__init__()
        # Usar chrome120 para evitar detección por Cloudflare en roomix.ai
        self.session = requests.Session(impersonate="chrome120")

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
        Scrapea las primeras 3 páginas de resultados de departamentos en CABA en roomix.ai
        (54 propiedades por página).
        """
        all_listings: List[Listing] = []
        seen_ids = set()
        logger.info(f"--- Iniciando extracción para {self.portal_name.upper()} ---")

        if MIN_ROOMS >= 2:
            endpoint_path = f"/buscar/alquilar/departamentos/en-capital-federal/{MIN_ROOMS}-ambientes"
        else:
            endpoint_path = "/buscar/alquilar/departamentos/en-capital-federal"

        for page in range(1, self.MAX_PAGES + 1):
            try:
                url = f"{self.BASE_URL}{endpoint_path}?page={page}"
                logger.info(f"[{self.portal_name}] GET {url}")
                html = self.fetch_html(url)
                if not html:
                    break

                parser = LexborHTMLParser(html)
                cards = parser.css('div[data-ph-capture-attribute-component="property-card"]')
                if not cards:
                    logger.info(f"[{self.portal_name}] No se encontraron tarjetas en página {page}.")
                    break

                for c in cards:
                    try:
                        property_id = c.attributes.get("data-ph-capture-attribute-property-id")
                        a = c.css_first('a[href*="/propiedad/"]')
                        if not a:
                            continue

                        href = (a.attributes.get("href") or "").strip()
                        if not href:
                            continue

                        if not property_id:
                            slug_match = re.search(r"/propiedad/[^/]+-([a-f0-9]+)", href)
                            property_id = slug_match.group(1) if slug_match else href

                        if property_id in seen_ids:
                            continue

                        # Ubicación y título
                        title_el = c.css_first("p[title]")
                        loc_text = title_el.text().strip() if title_el else ""
                        combined_text = f"{loc_text} {href}"
                        matched_nb = self._match_neighborhood(combined_text)
                        if not matched_nb:
                            continue

                        # Precios y Moneda
                        price_box = c.css_first(".mb-2.flex")
                        price_text = price_box.text().strip() if price_box else ""

                        # Descartar si el alquiler está expresado en dólares
                        rent_part = price_text.split("+")[0] if "+" in price_text else price_text
                        if "USD" in rent_part or "U" in rent_part:
                            continue

                        price = self.parse_ars_number(rent_part)
                        if price < 150000 or price > MAX_TOTAL_BUDGET_ARS:
                            continue

                        # Expensas
                        exp_part = price_text.split("+")[1] if "+" in price_text else ""
                        expenses = self.parse_ars_number(exp_part)
                        total_cost = price + expenses
                        if total_cost > MAX_TOTAL_BUDGET_ARS:
                            continue

                        # Descartar si el alquiler está a menos de 50.000 del tope sin expensas especificadas
                        if expenses <= 0.0 and price >= (MAX_TOTAL_BUDGET_ARS - 150000):
                            continue

                        seen_ids.add(property_id)

                        # Especificaciones (ambientes, m2)
                        specs_text = " ".join([s.text().strip() for s in c.css(".flex.items-center.gap-3 span")])
                        rooms = self.extract_rooms(specs_text) or MIN_ROOMS
                        if 0 < rooms < MIN_ROOMS:
                            continue

                        m2 = 0.0
                        m2_match = re.search(r"([\d\.]+)\s*m²", specs_text)
                        if m2_match:
                            try:
                                m2 = float(m2_match.group(1))
                            except ValueError:
                                pass

                        full_url = f"{self.BASE_URL}{href}"
                        title = f"Departamento en alquiler en {matched_nb} - {loc_text}".strip(" -")
                        has_balcony = False

                        # Consultar detalle para obtener datos precisos de balcón y título oficial
                        try:
                            time.sleep(0.5)
                            detail_html = self.fetch_html(full_url)
                            if detail_html:
                                detail_parser = LexborHTMLParser(detail_html)
                                h1 = detail_parser.css_first("h1")
                                if h1 and h1.text().strip():
                                    title = h1.text().strip()
                                has_balcony = self.detect_balcony(detail_html)
                            else:
                                has_balcony = self.detect_balcony(f"{loc_text} {href}")
                        except Exception:
                            has_balcony = self.detect_balcony(f"{loc_text} {href}")

                        # Imagen
                        img = c.css_first("img")
                        thumbnail_url = (img.attributes.get("src") or "").strip() if img else ""

                        listing = Listing(
                            id=f"roomix_{property_id}",
                            portal="roomix",
                            portal_id=property_id,
                            url=full_url,
                            title=title,
                            neighborhood=matched_nb,
                            address=loc_text,
                            price_ars=price,
                            expenses_ars=expenses,
                            total_cost_ars=total_cost,
                            rooms=rooms,
                            m2=m2,
                            has_balcony=has_balcony,
                            thumbnail_url=thumbnail_url,
                        )
                        all_listings.append(listing)
                        logger.info(
                            f"[{self.portal_name}] Extraído: {loc_text} ( + exp ) en {matched_nb}"
                        )

                    except Exception as card_err:
                        logger.debug(f"[{self.portal_name}] Error procesando tarjeta: {card_err}")
                        continue

                time.sleep(DELAY_BETWEEN_REQUESTS)

            except Exception as page_err:
                logger.error(f"[{self.portal_name}] Error en página {page}: {page_err}")
                break

        logger.info(f"[{self.portal_name}] Total extraídos en barrios objetivo: {len(all_listings)}")
        return all_listings
