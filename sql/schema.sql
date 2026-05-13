CREATE DATABASE IF NOT EXISTS boxoffice;
USE boxoffice;

CREATE TABLE IF NOT EXISTS movies (
    movie_id INT PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    release_year INT,
    revenue BIGINT DEFAULT 0,
    INDEX idx_revenue (revenue)
);.venv\Scripts\activate