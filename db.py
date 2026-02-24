"""
db.py — Database setup and connection
======================================
Uses SQLite locally and can be swapped for PostgreSQL on Render.
All tables are created here on first run.
"""

import sqlite3
import os

DATABASE = os.environ.get("DATABASE_URL", "cardpulse.db")


def get_db():
    """Returns a database connection with row_factory so columns are accessible by name."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """
    Creates all tables if they don't exist yet.
    Safe to call every time the app starts — won't overwrite existing data.
    """
    db = get_db()

    db.executescript("""

        -- Shops (card store owners)
        CREATE TABLE IF NOT EXISTS shops (
            id            TEXT PRIMARY KEY,
            shop_name     TEXT NOT NULL,
            owner_name    TEXT NOT NULL,
            email         TEXT UNIQUE NOT NULL,
            phone         TEXT,
            city          TEXT,
            state         TEXT,
            about         TEXT,
            slug          TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            logo          TEXT,
            banner        TEXT,
            membership    TEXT DEFAULT 'none',  -- 'none', 'monthly', 'yearly'
            status        TEXT DEFAULT 'pending', -- 'pending', 'approved', 'rejected'
            created_at    TEXT NOT NULL
        );

        -- Card listings
        CREATE TABLE IF NOT EXISTS listings (
            id          TEXT PRIMARY KEY,
            shop_id     TEXT NOT NULL,
            card_name   TEXT NOT NULL,
            condition   TEXT NOT NULL,
            price_cents INTEGER NOT NULL,
            quantity    INTEGER DEFAULT 1,
            notes       TEXT,
            image       TEXT,
            status      TEXT DEFAULT 'pending_payment', -- 'pending_payment', 'active', 'sold'
            created_at  TEXT NOT NULL,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        -- Bundle deals
        CREATE TABLE IF NOT EXISTS bundles (
            id          TEXT PRIMARY KEY,
            shop_id     TEXT NOT NULL,
            title       TEXT NOT NULL,
            description TEXT,
            price_cents INTEGER NOT NULL,
            items       TEXT NOT NULL,  -- comma-separated card names
            image       TEXT,
            created_at  TEXT NOT NULL,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

        -- Orders (completed purchases)
        CREATE TABLE IF NOT EXISTS orders (
            id                  TEXT PRIMARY KEY,
            listing_id          TEXT,
            bundle_id           TEXT,
            shop_id             TEXT NOT NULL,
            amount_cents        INTEGER NOT NULL,
            platform_fee_cents  INTEGER DEFAULT 0,
            stripe_session_id   TEXT,
            created_at          TEXT NOT NULL
        );

        -- Reviews left by buyers
        CREATE TABLE IF NOT EXISTS reviews (
            id            TEXT PRIMARY KEY,
            shop_id       TEXT NOT NULL,
            reviewer_name TEXT DEFAULT 'Anonymous',
            rating        INTEGER NOT NULL,  -- 1 to 5
            comment       TEXT,
            created_at    TEXT NOT NULL,
            FOREIGN KEY (shop_id) REFERENCES shops(id)
        );

    """)
    db.commit()
    db.close()
