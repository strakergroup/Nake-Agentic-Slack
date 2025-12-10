-- Create transcription_tasks table to track transcription task status and metadata
-- This table stores task information including bot_token for file downloads
--
-- Database: sitecommons (the MySQL database containing the sitecommons schema)
-- Run this migration against the sitecommons database
-- Example: mysql -h <host> -u <user> -p sitecommons < migrations/001_create_transcription_tasks.sql

USE sitecommons;

CREATE TABLE IF NOT EXISTS sitecommons.transcription_tasks (
    task_uuid VARCHAR(36) PRIMARY KEY,
    client_id VARCHAR(50) NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    download_url VARCHAR(500) NOT NULL,
    bot_token VARCHAR(255) NOT NULL,
    pipeline_type VARCHAR(50) NOT NULL DEFAULT 'transcribe',
    status ENUM('pending', 'processing', 'completed', 'failed') NOT NULL DEFAULT 'pending',
    error_message VARCHAR(1000) NULL,
    result_file_id VARCHAR(50) NULL,
    result_file_name VARCHAR(255) NULL,
    detected_language VARCHAR(10) NULL,
    tokens_consumed INT NOT NULL DEFAULT 0,
    extra_data JSON NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_client_id (client_id),
    INDEX idx_status (status),
    INDEX idx_created_at (created_at),
    INDEX idx_updated_at (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
