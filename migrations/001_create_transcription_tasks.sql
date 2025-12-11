-- Create transcription_tasks table to track transcription task status and metadata
-- This table stores task information including bot_token for file downloads,
-- performance metrics, and analytics data
--
-- Database: sitecommons (the MySQL database containing the sitecommons schema)
-- Run this migration against the sitecommons database
-- Example: mysql -h <host> -u <user> -p sitecommons < migrations/001_create_transcription_tasks.sql

USE sitecommons;

CREATE TABLE IF NOT EXISTS sitecommons.transcription_tasks (
    -- Primary key and identifiers
    task_uuid VARCHAR(36) PRIMARY KEY,
    client_id VARCHAR(50) NOT NULL,

    -- File information
    file_name VARCHAR(255) NOT NULL,
    download_url VARCHAR(500) NOT NULL,

    -- Configuration
    bot_token VARCHAR(255) NOT NULL COMMENT 'Slack bot token for file download',
    pipeline_type VARCHAR(50) NOT NULL DEFAULT 'transcribe',

    -- Status and results
    status ENUM('pending', 'processing', 'completed', 'failed') NOT NULL DEFAULT 'pending',
    error_message VARCHAR(1000) NULL,
    result_file_id VARCHAR(50) NULL,
    result_file_name VARCHAR(255) NULL,
    detected_language VARCHAR(10) NULL,

    -- Performance and analytics
    started_at DATETIME NULL COMMENT 'Timestamp when processing started',
    finished_at DATETIME NULL COMMENT 'Timestamp when processing finished',
    duration_ms INT NULL COMMENT 'Media duration in milliseconds',
    model VARCHAR(50) NULL COMMENT 'Model used (e.g., whisper-1)',
    service VARCHAR(50) NULL COMMENT 'Service provider (e.g., azure, google)',
    app_source VARCHAR(50) NULL COMMENT 'Source application (e.g., slack, teams)',

    -- Usage tracking
    tokens_consumed INT NOT NULL DEFAULT 0,

    -- Metadata
    extra_data JSON NULL,

    -- Timestamps
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    -- Indexes
    INDEX idx_client_id (client_id),
    INDEX idx_status (status),
    INDEX idx_created_at (created_at),
    INDEX idx_updated_at (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
