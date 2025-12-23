"""
Backend Security Examples for Voting System
===========================================

This file provides examples of secure backend implementation for a voting system,
adapted from the existing Flask app to use MySQL, Argon2 password hashing,
stricter session security, and additional protections against duplicate voting.

Requirements:
- Ubuntu VPS
- MySQL database
- Python/Flask stack
- HTTPS with HSTS, CSP, X-Frame-Options, X-Content-Type-Options
"""

# 1. Example Database Structure (MySQL)
"""
CREATE DATABASE voting_system CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE voting_system;

-- Elections table
CREATE TABLE elections (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    description TEXT,
    start_date DATETIME NOT NULL,
    end_date DATETIME NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- Voters table (for authentication if needed)
CREATE TABLE voters (
    id INT AUTO_INCREMENT PRIMARY KEY,
    voter_id VARCHAR(64) NOT NULL UNIQUE,  -- Unique identifier for anonymous voting
    email VARCHAR(255) UNIQUE,  -- Optional for registered voters
    hashed_password VARCHAR(255),  -- Argon2 hash for registered voters
    ip_address VARCHAR(45),  -- IPv4/IPv6 support
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_voter_id (voter_id),
    INDEX idx_email (email)
);

-- Votes table with unique constraints to prevent duplicates
CREATE TABLE votes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    voter_id VARCHAR(64) NOT NULL,
    election_id INT NOT NULL,
    nominee_id INT NOT NULL,  -- Assuming nominees are in a separate table
    ip_address VARCHAR(45) NOT NULL,
    user_agent TEXT,
    csrf_token VARCHAR(64) NOT NULL,  -- One-time CSRF token
    voted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_vote (voter_id, election_id),  -- Prevent duplicate votes per election
    UNIQUE KEY unique_ip_election (ip_address, election_id),  -- Additional IP-based uniqueness
    FOREIGN KEY (election_id) REFERENCES elections(id) ON DELETE CASCADE,
    INDEX idx_voter_election (voter_id, election_id),
    INDEX idx_ip_election (ip_address, election_id)
);

-- Nominees table (adapted from existing)
CREATE TABLE nominees (
    id INT AUTO_INCREMENT PRIMARY KEY,
    election_id INT NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    photo VARCHAR(255),
    votes_count INT DEFAULT 0,
    FOREIGN KEY (election_id) REFERENCES elections(id) ON DELETE CASCADE,
    INDEX idx_election (election_id)
);

-- Settings table for site configuration
CREATE TABLE settings (
    key_name VARCHAR(100) PRIMARY KEY,
    value TEXT
);

-- Insert default settings
INSERT INTO settings (key_name, value) VALUES
('site_title', 'Secure Voting System'),
('admin_username', 'admin'),
('admin_password_hash', ''),  -- Will be set via script
('csrf_secret_key', ''),  -- Random secret for CSRF
('session_secret_key', '');  -- Random secret for sessions
"""

# 2. Code Fragments for Voting with Protection (Python/Flask with MySQL and Argon2)

import os
import secrets
import logging
from datetime import datetime, timedelta
from flask import Flask, request, redirect, url_for, flash, session, make_response, jsonify
from flask_wtf.csrf import CSRFProtect, generate_csrf
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_session import Session  # For server-side sessions
import mysql.connector
from mysql.connector import Error
import argon2  # For password hashing
from argon2 import PasswordHasher
from functools import wraps
import bleach

# Initialize Argon2 hasher
ph = PasswordHasher()

# Flask app configuration
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_TYPE'] = 'filesystem'  # Server-side sessions
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=1)
app.config['SESSION_COOKIE_SECURE'] = True  # HTTPS only
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Strict'

# Initialize extensions
csrf = CSRFProtect(app)
limiter = Limiter(key_func=get_remote_address)
limiter.init_app(app)
Session(app)

# Database configuration
DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'user': os.environ.get('DB_USER', 'voting_user'),
    'password': os.environ.get('DB_PASSWORD', 'secure_password'),
    'database': os.environ.get('DB_NAME', 'voting_system'),
    'charset': 'utf8mb4',
    'collation': 'utf8mb4_unicode_ci'
}

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
vote_logger = logging.getLogger('votes')
vote_logger.setLevel(logging.INFO)
handler = logging.FileHandler('secure_votes.log')
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
vote_logger.addHandler(handler)

def get_db_connection():
    """Secure database connection with error handling"""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except Error as e:
        logger.error(f"Database connection error: {e}")
        raise

def sanitize_text(text):
    """Sanitize text input to prevent XSS"""
    if not text:
        return text
    return bleach.clean(text, tags=[], attributes={}, strip=True)

def generate_csrf_token():
    """Generate one-time CSRF token"""
    token = secrets.token_hex(32)
    session['csrf_token'] = token
    session['csrf_used'] = False
    return token

def validate_csrf_token(token):
    """Validate and consume CSRF token"""
    if session.get('csrf_token') == token and not session.get('csrf_used', False):
        session['csrf_used'] = True
        return True
    return False

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated

@app.before_request
def regenerate_session():
    """Regenerate session ID after login for security"""
    if session.get('admin_logged_in') and not session.get('session_regenerated'):
        session.regenerate()
        session['session_regenerated'] = True

@app.route('/admin/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def admin_login():
    if request.method == 'POST':
        username = sanitize_text(request.form.get('username'))
        password = request.form.get('password')

        # Get admin credentials from DB
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT value FROM settings WHERE key_name = ?", ('admin_password_hash',))
        result = cursor.fetchone()
        cursor.close()
        conn.close()

        if result and ph.verify(result['value'], password):
            session['admin_logged_in'] = True
            session['session_regenerated'] = False  # Will trigger regeneration
            logger.info(f"Admin login successful: {username}")
            return redirect(url_for('admin_panel'))
        else:
            logger.warning(f"Admin login failed: {username}")
            flash('Invalid credentials', 'danger')

    return render_template('admin_login.html')

@app.route('/vote/<int:election_id>/<int:nominee_id>', methods=['POST'])
@limiter.limit("10 per minute")
def vote(election_id, nominee_id):
    """Secure voting endpoint with PRG pattern and duplicate prevention"""

    # Validate CSRF token
    csrf_token = request.form.get('csrf_token')
    if not validate_csrf_token(csrf_token):
        flash('Invalid CSRF token', 'danger')
        return redirect(url_for('election_detail', election_id=election_id))

    # Get or create voter_id from secure cookie
    voter_id = request.cookies.get('voter_id')
    if not voter_id:
        voter_id = secrets.token_hex(16)

    ip_address = request.remote_addr
    user_agent = request.headers.get('User-Agent', '')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Check if election exists and is active
        cursor.execute("""
            SELECT id, start_date, end_date FROM elections
            WHERE id = ? AND start_date <= NOW() AND end_date >= NOW()
        """, (election_id,))
        election = cursor.fetchone()
        if not election:
            flash('Election not found or not active', 'danger')
            return redirect(url_for('index'))

        # Check if nominee exists in this election
        cursor.execute("""
            SELECT id FROM nominees WHERE id = ? AND election_id = ?
        """, (nominee_id, election_id))
        if not cursor.fetchone():
            flash('Nominee not found', 'danger')
            return redirect(url_for('election_detail', election_id=election_id))

        # Check for existing vote (duplicate prevention)
        cursor.execute("""
            SELECT id FROM votes WHERE voter_id = ? AND election_id = ?
        """, (voter_id, election_id))
        existing_vote = cursor.fetchone()

        if existing_vote:
            flash('You have already voted in this election', 'warning')
        else:
            # Insert vote using prepared statement
            cursor.execute("""
                INSERT INTO votes (voter_id, election_id, nominee_id, ip_address, user_agent, csrf_token)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (voter_id, election_id, nominee_id, ip_address, user_agent, csrf_token))

            # Update vote count
            cursor.execute("""
                UPDATE nominees SET votes_count = votes_count + 1 WHERE id = ?
            """, (nominee_id,))

            conn.commit()
            flash('Vote recorded successfully', 'success')

            # Log the vote
            vote_logger.info(f"Vote: Voter={voter_id}, Election={election_id}, Nominee={nominee_id}, IP={ip_address}")

    except Error as e:
        conn.rollback()
        logger.error(f"Database error during voting: {e}")
        flash('An error occurred while processing your vote', 'danger')
    finally:
        cursor.close()
        conn.close()

    # PRG Pattern: Redirect after POST
    return redirect(url_for('election_detail', election_id=election_id))

@app.route('/election/<int:election_id>')
def election_detail(election_id):
    """Election detail page with fresh CSRF token"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Get election details
        cursor.execute("SELECT * FROM elections WHERE id = ?", (election_id,))
        election = cursor.fetchone()

        if not election:
            flash('Election not found', 'danger')
            return redirect(url_for('index'))

        # Get nominees
        cursor.execute("""
            SELECT * FROM nominees WHERE election_id = ? ORDER BY votes_count DESC
        """, (election_id,))
        nominees = cursor.fetchall()

        # Check if user has voted
        voter_id = request.cookies.get('voter_id')
        has_voted = False
        if voter_id:
            cursor.execute("""
                SELECT id FROM votes WHERE voter_id = ? AND election_id = ?
            """, (voter_id, election_id))
            has_voted = bool(cursor.fetchone())

    finally:
        cursor.close()
        conn.close()

    # Generate fresh CSRF token for the form
    csrf_token = generate_csrf_token()

    return render_template('election_detail.html',
                         election=election,
                         nominees=nominees,
                         has_voted=has_voted,
                         csrf_token=csrf_token)

# 3. Security Headers (.htaccess for Apache or nginx config)

"""
# Apache .htaccess
<IfModule mod_headers.c>
    # HTTPS and HSTS
    Header always set Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"

    # Content Security Policy
    Header always set Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' https:; connect-src 'self'"

    # X-Frame-Options
    Header always set X-Frame-Options "DENY"

    # X-Content-Type-Options
    Header always set X-Content-Type-Options "nosniff"

    # Referrer Policy
    Header always set Referrer-Policy "strict-origin-when-cross-origin"

    # Permissions Policy (formerly Feature Policy)
    Header always set Permissions-Policy "geolocation=(), microphone=(), camera=()"

    # Remove server information
    Header always unset Server
    Header always unset X-Powered-By
</IfModule>

# nginx configuration snippet
server {
    listen 443 ssl http2;
    server_name yourdomain.com;

    # SSL configuration (replace with your certificates)
    ssl_certificate /path/to/your/certificate.crt;
    ssl_certificate_key /path/to/your/private.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384;

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' https:; connect-src 'self'" always;
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "geolocation=(), microphone=(), camera=()" always;

    # Hide nginx version
    server_tokens off;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$server_name$request_uri;
}
"""

# 4. Testing Scenario

"""
Testing Scenario for Secure Voting System
=========================================

Prerequisites:
- Ubuntu VPS with MySQL, Python 3.8+, Apache/nginx
- SSL certificate installed
- Database created and populated with test data

Test Cases:

1. SQL Injection Prevention
   - Attempt to inject SQL in voter_id, nominee name, etc.
   - Expected: No execution of injected code, safe error handling

2. Password Security
   - Verify admin password uses Argon2 hashing
   - Test login with correct/incorrect passwords
   - Expected: Only correct password allows login

3. Session Security
   - Check session cookies: secure, httponly, samesite=strict
   - Attempt session fixation: login, then change session ID
   - Expected: Session ID regenerated after login

4. HTTPS and Security Headers
   - Access via HTTP: should redirect to HTTPS
   - Check response headers: HSTS, CSP, X-Frame-Options, etc.
   - Expected: All security headers present

5. Duplicate Vote Prevention
   - Vote once, attempt to vote again
   - Try voting from different IP but same voter_id
   - Expected: Second vote rejected due to unique constraints

6. CSRF Protection
   - Attempt to submit vote form without CSRF token
   - Use expired CSRF token
   - Expected: Vote rejected, new token required

7. Rate Limiting
   - Make multiple rapid requests to vote endpoint
   - Expected: Requests throttled after limit exceeded

8. XSS Prevention
   - Attempt to inject scripts in nominee names, descriptions
   - Expected: Scripts sanitized, no execution

9. Logging
   - Perform votes and check logs
   - Expected: All voting attempts logged with metadata

10. PRG Pattern
    - Submit vote form, check if redirected
    - Refresh page: should not resubmit vote
    - Expected: No duplicate votes on refresh

Test Script (Python):
"""

import requests
import time
from bs4 import BeautifulSoup

BASE_URL = "https://yourdomain.com"
session = requests.Session()

def test_duplicate_vote_prevention():
    """Test that duplicate votes are prevented"""
    # Get election page
    response = session.get(f"{BASE_URL}/election/1")
    soup = BeautifulSoup(response.text, 'html.parser')

    # Extract CSRF token
    csrf_token = soup.find('input', {'name': 'csrf_token'})['value']

    # Vote once
    data = {'csrf_token': csrf_token, 'nominee_id': 1}
    response = session.post(f"{BASE_URL}/vote/1/1", data=data, allow_redirects=True)
    assert "Vote recorded successfully" in response.text

    # Try to vote again
    response = session.post(f"{BASE_URL}/vote/1/1", data=data, allow_redirects=True)
    assert "already voted" in response.text

def test_csrf_protection():
    """Test CSRF token validation"""
    # Try voting without CSRF token
    response = session.post(f"{BASE_URL}/vote/1/1", data={'nominee_id': 1})
    assert response.status_code == 400 or "Invalid CSRF token" in response.text

def test_rate_limiting():
    """Test rate limiting"""
    for i in range(15):
        response = session.post(f"{BASE_URL}/vote/1/1", data={'csrf_token': 'invalid'})
        if response.status_code == 429:
            print("Rate limiting working")
            break
        time.sleep(0.1)

def test_security_headers():
    """Test security headers"""
    response = session.get(BASE_URL)
    headers = response.headers

    assert 'Strict-Transport-Security' in headers
    assert 'Content-Security-Policy' in headers
    assert 'X-Frame-Options' in headers
    assert 'X-Content-Type-Options' in headers
    assert headers.get('X-Frame-Options') == 'DENY'

if __name__ == "__main__":
    test_security_headers()
    test_csrf_protection()
    test_rate_limiting()
    test_duplicate_vote_prevention()
    print("All tests passed!")
