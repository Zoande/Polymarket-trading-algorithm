-- ============================================================================
-- Polymarket Trading Bot - Supabase Database Schema
-- ============================================================================
-- 
-- HOW TO USE:
-- 1. Go to your Supabase project dashboard
-- 2. Click on "SQL Editor" in the left sidebar
-- 3. Click "New query"
-- 4. Paste this entire file and click "Run"
--
-- ============================================================================
-- Enable UUID extension if not already enabled
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
-- ============================================================================
-- Table: bot_state
-- Stores the main bot state (cash, stats, etc.)
-- ============================================================================
CREATE TABLE IF NOT EXISTS bot_state (
    instance_id TEXT PRIMARY KEY,
    cash_balance DOUBLE PRECISION DEFAULT 10000,
    total_trades INTEGER DEFAULT 0,
    winning_trades INTEGER DEFAULT 0,
    losing_trades INTEGER DEFAULT 0,
    total_pnl DOUBLE PRECISION DEFAULT 0,
    trade_counter INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
-- ============================================================================
-- Table: open_trades
-- Stores currently open positions
-- ============================================================================
CREATE TABLE IF NOT EXISTS open_trades (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    instance_id TEXT NOT NULL,
    trade_id TEXT NOT NULL,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(instance_id, trade_id)
);
-- Index for faster queries
CREATE INDEX IF NOT EXISTS idx_open_trades_instance ON open_trades(instance_id);
-- ============================================================================
-- Table: closed_trades
-- Stores historical closed trades
-- ============================================================================
CREATE TABLE IF NOT EXISTS closed_trades (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    instance_id TEXT NOT NULL,
    trade_id TEXT NOT NULL,
    data JSONB NOT NULL,
    closed_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(instance_id, trade_id)
);
-- Index for faster queries
CREATE INDEX IF NOT EXISTS idx_closed_trades_instance ON closed_trades(instance_id);
CREATE INDEX IF NOT EXISTS idx_closed_trades_closed_at ON closed_trades(closed_at);
-- ============================================================================
-- Table: trade_log
-- Stores recent trade activity log
-- ============================================================================
CREATE TABLE IF NOT EXISTS trade_log (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    instance_id TEXT NOT NULL,
    data JSONB NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
-- Index for faster queries
CREATE INDEX IF NOT EXISTS idx_trade_log_instance ON trade_log(instance_id);
CREATE INDEX IF NOT EXISTS idx_trade_log_timestamp ON trade_log(timestamp);
-- ============================================================================
-- Table: market_categories
-- Stores market category mappings
-- ============================================================================
CREATE TABLE IF NOT EXISTS market_categories (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    instance_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    category TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(instance_id, market_id)
);
-- Index for faster queries
CREATE INDEX IF NOT EXISTS idx_market_categories_instance ON market_categories(instance_id);
-- ============================================================================
-- Table: blacklist
-- Stores blacklisted markets
-- ============================================================================
CREATE TABLE IF NOT EXISTS blacklist (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    instance_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(instance_id, market_id)
);
-- Index for faster queries
CREATE INDEX IF NOT EXISTS idx_blacklist_instance ON blacklist(instance_id);
-- ============================================================================
-- Enable Row Level Security (RLS) for all tables
-- This ensures data isolation between different API keys
-- ============================================================================
-- For now, we'll allow all operations with the anon key
-- In production, you might want to add more restrictive policies
ALTER TABLE bot_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE open_trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE closed_trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE trade_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE market_categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE blacklist ENABLE ROW LEVEL SECURITY;
-- Create policies to allow all operations (for simplicity)
-- You can make these more restrictive based on your needs
CREATE POLICY "Allow all operations on bot_state" ON bot_state FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all operations on open_trades" ON open_trades FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all operations on closed_trades" ON closed_trades FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all operations on trade_log" ON trade_log FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all operations on market_categories" ON market_categories FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all operations on blacklist" ON blacklist FOR ALL USING (true) WITH CHECK (true);
-- ============================================================================
-- Done! Your database is ready.
-- ============================================================================