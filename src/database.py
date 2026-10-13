from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional, Tuple
from src.config import DATABASE_PATH, Listing


class Database:
    """Manages SQLite storage for listings, price history, and deduplication."""

    def __init__(self, db_path: Path = DATABASE_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # Enable WAL mode for concurrency and performance
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        """Create tables and indexes if they do not exist."""
        with self._get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS listings (
                    id TEXT PRIMARY KEY,
                    portal TEXT NOT NULL,
                    portal_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    title TEXT,
                    neighborhood TEXT,
                    address TEXT,
                    price_ars REAL,
                    expenses_ars REAL,
                    total_cost_ars REAL,
                    rooms INTEGER,
                    m2 REAL,
                    has_balcony BOOLEAN DEFAULT 0,
                    thumbnail_url TEXT,
                    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    listing_id TEXT NOT NULL,
                    price_ars REAL NOT NULL,
                    expenses_ars REAL,
                    total_cost_ars REAL NOT NULL,
                    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(listing_id) REFERENCES listings(id)
                );

                CREATE INDEX IF NOT EXISTS idx_listings_portal_portal_id 
                ON listings(portal, portal_id);

                CREATE INDEX IF NOT EXISTS idx_price_history_listing_id 
                ON price_history(listing_id);
                """
            )

    def get_cached_expenses(self, listing_id: str, current_price: float) -> Optional[float]:
        """Returns cached expenses if listing exists with the same price, avoiding repeated HTTP fetches."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT price_ars, expenses_ars FROM listings WHERE id = ?",
                (listing_id,),
            )
            row = cursor.fetchone()
            if row and row["price_ars"] == current_price and row["expenses_ars"] is not None:
                return float(row["expenses_ars"])
        return None

    def process_listing(self, listing: Listing) -> Tuple[str, Optional[float]]:
        """
        Evaluate listing against DB records.
        Returns:
            (status, previous_price):
            - status: "NEW", "PRICE_DROP", or "EXISTING"
            - previous_price: float if PRICE_DROP, else None
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, price_ars, expenses_ars, total_cost_ars FROM listings WHERE id = ?",
                (listing.id,),
            )
            row = cursor.fetchone()

            if row is None:
                # NEW listing
                cursor.execute(
                    """
                    INSERT INTO listings (
                        id, portal, portal_id, url, title, neighborhood, address,
                        price_ars, expenses_ars, total_cost_ars, rooms, m2,
                        has_balcony, thumbnail_url, first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        listing.id,
                        listing.portal,
                        listing.portal_id,
                        listing.url,
                        listing.title,
                        listing.neighborhood,
                        listing.address,
                        listing.price_ars,
                        listing.expenses_ars,
                        listing.total_cost_ars,
                        listing.rooms,
                        listing.m2,
                        1 if listing.has_balcony else 0,
                        listing.thumbnail_url,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO price_history (listing_id, price_ars, expenses_ars, total_cost_ars)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        listing.id,
                        listing.price_ars,
                        listing.expenses_ars,
                        listing.total_cost_ars,
                    ),
                )
                conn.commit()
                return "NEW", None

            # Existing listing: Check if price dropped
            old_price = float(row["price_ars"] or 0.0)
            old_total = float(row["total_cost_ars"] or 0.0)

            # Usamos total_cost_ars o price_ars para evaluar la rebaja
            current_total = listing.total_cost_ars

            # Condición de rebaja significativa (más de 50000 ARS de diferencia para evitar decimales)
            if current_total > 0 and old_total > 0 and (old_total - current_total) >= 50000:
                cursor.execute(
                    """
                    UPDATE listings
                    SET price_ars = ?, expenses_ars = ?, total_cost_ars = ?,
                        title = ?, address = ?, thumbnail_url = ?, last_seen_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        listing.price_ars,
                        listing.expenses_ars,
                        listing.total_cost_ars,
                        listing.title,
                        listing.address,
                        listing.thumbnail_url,
                        listing.id,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO price_history (listing_id, price_ars, expenses_ars, total_cost_ars)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        listing.id,
                        listing.price_ars,
                        listing.expenses_ars,
                        listing.total_cost_ars,
                    ),
                )
                conn.commit()
                return "PRICE_DROP", old_total

            # Otherwise just update last_seen_at and metadata
            cursor.execute(
                """
                UPDATE listings
                SET last_seen_at = CURRENT_TIMESTAMP,
                    url = ?,
                    title = COALESCE(?, title),
                    thumbnail_url = CASE WHEN thumbnail_url = '' THEN ? ELSE thumbnail_url END,
                    expenses_ars = CASE WHEN expenses_ars = 0 THEN ? ELSE expenses_ars END,
                    total_cost_ars = CASE WHEN expenses_ars = 0 AND ? > 0 THEN price_ars + ? ELSE total_cost_ars END
                WHERE id = ?
                """,
                (
                    listing.url,
                    listing.title,
                    listing.thumbnail_url,
                    listing.expenses_ars,
                    listing.expenses_ars,
                    listing.expenses_ars,
                    listing.id,
                ),
            )
            conn.commit()
            return "EXISTING", None

    def get_total_listings(self) -> int:
        """Returns total listings stored in the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM listings")
            return cursor.fetchone()[0]
