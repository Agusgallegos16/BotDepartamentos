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


class MercadoLibreScraper(BaseScraper):
    """Scraper for Mercado Libre Inmuebles."""

    portal_name: str = "mercadolibre"
    BASE_URL = "https://inmuebles.mercadolibre.com.ar"

    def _build_url(self, neighborhood_slug: str) -> str:
        max_budget = int(MAX_TOTAL_BUDGET_ARS)
        # En Argentina: 2 ambientes = 1 dormitorio, 3 ambientes = 2 dormitorios.
        # El filtro de URL de Mercado Libre es BEDROOMS (dormitorios).
        min_bedrooms = max(1, MIN_ROOMS - 1)
        clean_slug = neighborhood_slug.replace("-capital-federal", "")
        return (
            f"{self.BASE_URL}/departamentos/alquiler/capital-federal/{clean_slug}/"
            f"_PriceRange_0ARS-{max_budget}_BEDROOMS_{min_bedrooms}-*"
        )

    def _fetch_detail_expenses(self, detail_url: str) -> float:
        """Extrae el valor de expensas accediendo a la ficha de la propiedad."""
        try:
            html = self.fetch_html(detail_url)
            if not html:
                return 0.0
            parser = LexborHTMLParser(html)

            # 1. Elemento directo de expensas (ej: 'Expensas desde $ 135.000')
            exp_node = parser.css_first(
                "[class*='ui-pdp-maintenance-fee'], [class*='maintenance-fee']"
            )
            if exp_node:
                return self.parse_ars_number(exp_node.text(strip=True))

            # 2. Fila en tabla de especificaciones
            for tr in parser.css("tr.ui-vpp-striped-specs__row, tr.andes-table__row, tr"):
                txt = tr.text(strip=True)
                if "expensa" in txt.lower():
                    return self.parse_ars_number(txt)

            # 3. Regex en texto general
            m = re.search(
                r"expensas[^\d\n\r]{0,25}(?:desde\s*)?\$?\s*([\d\.]+)",
                html,
                re.IGNORECASE,
            )
            if m:
                return self.parse_ars_number(m.group(0))

        except Exception as e:
            logger.debug(f"[{self.portal_name}] Error obteniendo expensas de detalle: {e}")
        return 0.0

    def scrape_neighborhood(self, neighborhood: str) -> List[Listing]:
        slug_info = NEIGHBORHOOD_SLUGS.get(neighborhood)
        if not slug_info:
            logger.warning(f"[{self.portal_name}] Barrio no mapeado: '{neighborhood}'")
            return []

        url = self._build_url(slug_info["mercadolibre"])
        html_content = self.fetch_html(url)
        if not html_content:
            return []

        parser = LexborHTMLParser(html_content)
        cards = parser.css("li.ui-search-layout__item")

        listings: List[Listing] = []

        for card in cards:
            try:
                # Enlace y Portal ID
                link_elem = card.css_first("a.poly-component__title, a.ui-search-link, a")
                if not link_elem:
                    continue

                full_url = link_elem.attributes.get("href") or ""
                if not full_url:
                    continue

                # Limpiar fragmentos y query params extensos de ML
                if "#" in full_url:
                    full_url = full_url.split("#")[0]
                if "?" in full_url:
                    full_url = full_url.split("?")[0]

                # ID extraído de la URL (ej: MLA-2168563171 o MLA123456)
                id_match = re.search(r"(MLA-?\d+)", full_url, re.IGNORECASE)
                portal_id = (
                    id_match.group(1).replace("-", "")
                    if id_match
                    else str(hash(full_url))
                )

                # Título
                title_elem = card.css_first(
                    ".poly-component__title, .ui-search-item__title, h2"
                )
                title = title_elem.text(strip=True) if title_elem else "Departamento"

                # Precio
                price_elem = card.css_first(".andes-money-amount__fraction")
                raw_price = price_elem.text(strip=True) if price_elem else ""
                price_ars = self.parse_ars_number(raw_price)

                if price_ars <= 0:
                    continue

                # Expensas: si no están en la tarjeta, consultar la página del aviso
                expenses_ars = 0.0
                card_text = card.text() or ""
                exp_match = re.search(r"expensas\s*[:$]?\s*([\d\.]+)", card_text, re.IGNORECASE)
                if exp_match:
                    expenses_ars = self.parse_ars_number(exp_match.group(1))

                if expenses_ars <= 0.0:
                    expenses_ars = self._fetch_detail_expenses(full_url)

                # Si con expensas supera el presupuesto máximo, descartar
                if (price_ars + expenses_ars) > MAX_TOTAL_BUDGET_ARS:
                    continue

                # Ubicación / Dirección
                loc_elem = card.css_first(
                    ".poly-component__location, .ui-search-item__location"
                )
                address = loc_elem.text(strip=True) if loc_elem else slug_info["name"]

                # Atributos (ambientes, m2)
                attr_items = card.css(
                    ".poly-attributes-list__item, ul.ui-search-card-attributes li"
                )
                attr_texts = [a.text(strip=True) for a in attr_items]
                combined_text = " ".join([title, address] + attr_texts)

                rooms = 0
                m2 = 0.0

                for attr in attr_texts:
                    attr_lower = attr.lower()
                    if "amb" in attr_lower:
                        rooms = self.extract_rooms(attr)
                    elif "m²" in attr_lower or "m2" in attr_lower:
                        m2_match = re.search(r"(\d+)", attr)
                        if m2_match:
                            m2 = float(m2_match.group(1))

                if rooms == 0:
                    rooms = self.extract_rooms(combined_text)

                if 0 < rooms < MIN_ROOMS:
                    continue

                has_balcony = self.detect_balcony(combined_text)

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
                listings.append(listing)

            except Exception as e:
                logger.debug(f"[{self.portal_name}] Error en tarjeta ML: {e}")
                continue

        return listings
