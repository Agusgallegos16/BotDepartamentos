from __future__ import annotations

import logging
import re
import time
from typing import List

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


class DirectoAlDuenoScraper(BaseScraper):
    """Scraper para directoaldueno.com.ar (WordPress WP Estate)."""

    portal_name: str = "directoaldueno"
    BASE_URL = "https://directoaldueno.com.ar"
    MAX_PAGES = 7

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

    def _fetch_property_details(self, url: str) -> dict:
        """
        Obtiene campos estructurados de la página individual de la propiedad:
        expensas, ambientes exactos, dormitorios y presencia de balcón.
        """
        details = {
            "expenses": 0.0,
            "rooms": 0,
            "bedrooms": 0,
            "m2": 0.0,
            "has_balcony": False,
            "address": "",
        }
        try:
            html = self.fetch_html(url)
            if not html:
                return details

            parser = LexborHTMLParser(html)
            text_full = parser.text()

            # 1. Balcón
            details["has_balcony"] = self.detect_balcony(text_full)

            # 2. Detalles estructurados en .listing_detail
            for el in parser.css(".listing_detail, .property_custom_detail, .panel-body div"):
                t = el.text().strip()
                t_lower = t.lower()

                if "expensas:" in t_lower:
                    val_str = t.split(":", 1)[-1].strip()
                    parsed_exp = self.parse_ars_number(val_str)
                    if 0 < parsed_exp < 1000:
                        parsed_exp *= 1000
                    details["expenses"] = parsed_exp
                elif "sin expensas" in t_lower:
                    details["expenses"] = 0.0
                elif "ambientes:" in t_lower:
                    match = re.search(r"(\d+)", t)
                    if match:
                        details["rooms"] = int(match.group(1))
                elif "dormitorios:" in t_lower or "habitaciones:" in t_lower:
                    match = re.search(r"(\d+)", t)
                    if match:
                        details["bedrooms"] = int(match.group(1))
                elif "superficie total" in t_lower or "tamaño de la propiedad" in t_lower:
                    match = re.search(r"([\d\.]+)\s*m", t)
                    if match and details["m2"] == 0.0:
                        try:
                            details["m2"] = float(match.group(1))
                        except ValueError:
                            pass
                elif "dirección:" in t_lower or "direccion:" in t_lower:
                    details["address"] = t.split(":", 1)[-1].strip()

            # Si no se encontró el valor explícito de ambientes pero sí dormitorios:
            # En Argentina: 2 dormitorios = 3 ambientes, 1 dormitorio = 2 ambientes
            if details["rooms"] == 0 and details["bedrooms"] > 0:
                details["rooms"] = details["bedrooms"] + 1

        except Exception as e:
            logger.debug(f"[{self.portal_name}] Error obteniendo detalles de {url}: {e}")

        return details

    def scrape_all(self) -> List[Listing]:
        """
        Scrapea las primeras 3 páginas de resultados de alquileres de departamentos
        en directoaldueno.com.ar.
        """
        all_listings: List[Listing] = []
        seen_ids = set()
        logger.info(f"--- Iniciando extracción para {self.portal_name.upper()} ---")

        for page in range(1, self.MAX_PAGES + 1):
            try:
                if page == 1:
                    url = f"{self.BASE_URL}/resultados-de-busqueda/?filter_search_action%5B%5D=alquilar&filter_search_type%5B%5D=departamento"
                else:
                    url = f"{self.BASE_URL}/resultados-de-busqueda/page/{page}/?filter_search_action%5B%5D=alquilar&filter_search_type%5B%5D=departamento"

                html = self.fetch_html(url)
                if not html:
                    break

                parser = LexborHTMLParser(html)
                cards = parser.css("div.property_listing")
                if not cards:
                    logger.info(f"[{self.portal_name}] No se encontraron tarjetas en página {page}.")
                    break

                for c in cards:
                    try:
                        title_el = c.css_first("h4 a")
                        if not title_el:
                            continue

                        title = title_el.text().strip()
                        listing_url = (title_el.attributes.get("href") or "").strip()
                        if not listing_url:
                            continue

                        # ID: postid de favoritos o del slug de URL
                        fav_el = c.css_first("span.icon-fav[data-postid]")
                        if fav_el and fav_el.attributes.get("data-postid"):
                            property_id = fav_el.attributes.get("data-postid").strip()
                        else:
                            # Extraer del slug
                            slug_match = re.search(r"/propiedades/([^/]+)/", listing_url)
                            property_id = slug_match.group(1) if slug_match else listing_url

                        if property_id in seen_ids:
                            continue

                        # Moneda y Precio desde la tarjeta
                        price_el = c.css_first(".listing_unit_price_wrapper")
                        price_text = price_el.text().strip() if price_el else ""

                        # Descartar si es en dólares
                        if "USD" in price_text or "U" in price_text:
                            continue

                        price = self.parse_ars_number(price_text)
                        if price < 150000 or price > MAX_TOTAL_BUDGET_ARS:
                            continue

                        # Ubicación preliminar
                        loc_el = c.css_first(".property_location_image")
                        loc_text = loc_el.text().strip() if loc_el else ""
                        combined_text = f"{loc_text} {title} {listing_url}"
                        matched_nb = self._match_neighborhood(combined_text)
                        if not matched_nb:
                            continue

                        # Ambientes preliminares: .inforoom suele ser dormitorios
                        room_el = c.css_first(".inforoom")
                        raw_bedrooms = int(room_el.text().strip()) if room_el and room_el.text().strip().isdigit() else 0
                        raw_rooms = self.extract_rooms(title)

                        # Si indica 1 dormitorio y no menciona 3 ambientes en el título, y MIN_ROOMS >= 3, descartar
                        if raw_bedrooms > 0 and (raw_bedrooms + 1) < MIN_ROOMS and raw_rooms < MIN_ROOMS:
                            continue

                        # Obtener detalles completos de la página individual
                        time.sleep(1.0)
                        details = self._fetch_property_details(listing_url)

                        expenses = details["expenses"]
                        total_cost = price + expenses
                        if total_cost > MAX_TOTAL_BUDGET_ARS:
                            continue

                        # Descartar si el alquiler está a menos de 50.000 del tope sin expensas especificadas
                        if expenses <= 0.0 and price >= (MAX_TOTAL_BUDGET_ARS - 150000):
                            continue

                        final_rooms = details["rooms"] or raw_rooms or (raw_bedrooms + 1 if raw_bedrooms else 0)
                        if 0 < final_rooms < MIN_ROOMS:
                            continue

                        seen_ids.add(property_id)

                        # Superficie y Balcón
                        size_el = c.css_first(".infosize")
                        card_m2 = 0.0
                        if size_el:
                            m2_match = re.search(r"([\d\.]+)", size_el.text())
                            if m2_match:
                                try:
                                    card_m2 = float(m2_match.group(1))
                                except ValueError:
                                    pass

                        m2 = details["m2"] or card_m2
                        has_balcony = details["has_balcony"] or self.detect_balcony(title)

                        # Imagen
                        img_el = c.css_first("img")
                        thumbnail_url = ""
                        if img_el:
                            thumbnail_url = (
                                img_el.attributes.get("data-src")
                                or img_el.attributes.get("src")
                                or ""
                            )

                        address = details["address"] or loc_text

                        listing = Listing(
                            id=f"directoaldueno_{property_id}",
                            portal="directoaldueno",
                            portal_id=property_id,
                            url=listing_url,
                            title=title,
                            neighborhood=matched_nb,
                            address=address,
                            price_ars=price,
                            expenses_ars=expenses,
                            total_cost_ars=total_cost,
                            rooms=final_rooms,
                            m2=m2,
                            has_balcony=has_balcony,
                            thumbnail_url=thumbnail_url,
                        )
                        all_listings.append(listing)
                        logger.info(f"[{self.portal_name}] Extraído: {title} ( + exp ) en {matched_nb}")

                    except Exception as card_err:
                        logger.debug(f"[{self.portal_name}] Error procesando tarjeta: {card_err}")
                        continue

                time.sleep(DELAY_BETWEEN_REQUESTS)

            except Exception as page_err:
                logger.error(f"[{self.portal_name}] Error en página {page}: {page_err}")
                break

        logger.info(f"[{self.portal_name}] Total extraídos en barrios objetivo: {len(all_listings)}")
        return all_listings
