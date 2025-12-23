from flask import Flask, render_template, request, redirect, url_for, flash, session, make_response
import sqlite3
import os
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
import secrets
from PIL import Image
from io import BytesIO
from datetime import datetime, timedelta
import logging
import time
import random
from flask_wtf.csrf import CSRFProtect, generate_csrf
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import bleach
import bcrypt

# Настройка санитизации
def sanitize_text(text):
    """Санитизация текста для предотвращения XSS"""
    if not text:
        return text
    return bleach.clean(text, tags=[], attributes={}, strip=True)

def sanitize_html(text):
    """Санитизация HTML с разрешением базовых тегов"""
    if not text:
        return text
    allowed_tags = ['p', 'br', 'strong', 'em', 'u', 'a']
    allowed_attrs = {'a': ['href', 'title']}
    return bleach.clean(text, tags=allowed_tags, attributes=allowed_attrs, strip=True)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Настройка логирования голосований в файл
vote_logger = logging.getLogger('votes')
vote_logger.setLevel(logging.INFO)
vote_handler = logging.FileHandler('votes.log')
vote_formatter = logging.Formatter('%(asctime)s - %(message)s')
vote_handler.setFormatter(vote_formatter)
vote_logger.addHandler(vote_handler)

# Настройки приложения
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', os.urandom(24).hex())
app.config["DATABASE"] = os.path.join(app.instance_path, "site.db")
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024  # Ограничение загрузки 8MB
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=1)  # Время жизни сессии

# Инициализация CSRF защиты
csrf = CSRFProtect(app)

# Инициализация лимитера
limiter = Limiter(key_func=get_remote_address)
limiter.init_app(app)

# Загрузка конфигурации из переменных окружения
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD_HASH = os.environ.get('ADMIN_PASSWORD_HASH', bcrypt.hashpw('admin'.encode('utf-8'), bcrypt.gensalt()).decode('utf-8'))
# По умолчанию логин: admin, пароль: admin

# Допустимые расширения файлов
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
UPLOAD_FOLDER = os.path.join(app.static_folder or 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(app.instance_path, exist_ok=True)

def get_db():
    """Создает и возвращает соединение с базой данных"""
    try:
        conn = sqlite3.connect(app.config["DATABASE"])
        conn.row_factory = sqlite3.Row
        # Включение внешних ключей для SQLite
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    except sqlite3.Error as e:
        logger.error(f"Ошибка подключения к базе данных: {e}")
        raise

def init_db():
    """Инициализация базы данных и создание таблиц, если они не существуют"""
    with get_db() as db:
            # Включение журналирования WAL для лучшей производительности
            db.execute("PRAGMA journal_mode=WAL")
            
            # Создаем таблицу номинаций
            db.execute("""
                CREATE TABLE IF NOT EXISTS nominations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    statistics TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Add statistics column if it doesn't exist (for existing databases)
            try:
                db.execute("ALTER TABLE nominations ADD COLUMN statistics TEXT DEFAULT '{}'")
            except sqlite3.OperationalError:
                pass  # Column already exists
            # Создаем таблицу участников (номинантов)
            db.execute("""
                CREATE TABLE IF NOT EXISTS nominees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT,
                    description TEXT,
                    photo TEXT,
                    nomination_id INTEGER,
                    votes INTEGER DEFAULT 0,
                    FOREIGN KEY (nomination_id) REFERENCES nominations (id)
                )
            """)
            # Seed some sample nominations if table is empty
            cur = db.execute("SELECT COUNT(1) as cnt FROM nominations")
            cnt = cur.fetchone()[0]
            if cnt == 0:
                # Добавляем стандартные номинации Оскара
                db.executemany(
                    "INSERT INTO nominations (name, description) VALUES (?, ?)",
                    [
                        ("Лучший фильм", "Самый лучший фильм года по версии Американской киноакадемии"),
                        ("Лучший актер", "Лучшая мужская роль в главной роли"),
                        ("Лучшая актриса", "Лучшая женская роль в главной роли"),
                        ("Лучший режиссер", "Лучшая режиссура года"),
                        ("Лучший сценарий", "Лучший оригинальный или адаптированный сценарий")
                    ],
                )
                # Добавляем участников для каждой номинации
                nomination_ids = db.execute("SELECT id FROM nominations ORDER BY id").fetchall()
                nominee_data = [
                    # Лучший фильм
                    ("Оппенгеймер", "Фильм о создании атомной бомбы", nomination_ids[0]['id']),
                    ("Барби", "Комедийно-драматический фильм о кукле", nomination_ids[0]['id']),
                    ("Дюна: Часть вторая", "Научная фантастика", nomination_ids[0]['id']),
                    # Лучший актер
                    ("Киллиан Мёрфи", "Роль в фильме Оппенгеймер", nomination_ids[1]['id']),
                    ("Райан Гослинг", "Роль в фильме Барби", nomination_ids[1]['id']),
                    ("Пол Дано", "Роль в фильме Уэстсайдская история", nomination_ids[1]['id']),
                    # Лучшая актриса
                    ("Эмма Стоун", "Роль в фильме Дама дикого запада", nomination_ids[2]['id']),
                    ("Лили Гладон", "Роль в фильме Барби", nomination_ids[2]['id']),
                    ("Саори Хиросэ", "Роль в фильме Убийцы", nomination_ids[2]['id']),
                    # Лучший режиссер
                    ("Кристофер Нолан", "Режиссура фильма Оппенгеймер", nomination_ids[3]['id']),
                    ("Грета Гервиг", "Режиссура фильма Барби", nomination_ids[3]['id']),
                    ("Дени Вильнёв", "Режиссура фильма Дюна: Часть вторая", nomination_ids[3]['id']),
                    # Лучший сценарий
                    ("Человек-паук: Паутина вселенной", "Лучший оригинальный сценарий", nomination_ids[4]['id']),
                    ("Вторжение", "Лучший адаптированный сценарий", nomination_ids[4]['id']),
                    ("Грань", "Лучший оригинальный сценарий", nomination_ids[4]['id'])
                ]
                db.executemany(
                    "INSERT INTO nominees (name, description, nomination_id) VALUES (?, ?, ?)",
                nominee_data
            )
            # Settings table for site-wide editable values
            db.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            # Seed default settings
            cur = db.execute("SELECT 1 FROM settings WHERE key = 'site_title'")
            if not cur.fetchone():
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("site_title", "Голосование за Оскар"))
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("banner_text", "Оскар 2025 — голосуем за лучшее!") )
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("primary_color", "#0d6efd") )
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("header_image", "") )
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("background_image", "") )
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("use_background", "0") )
                # Make dark theme the default
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("theme", "dark") )
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("navbar_style", "transparent") )
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("candidates_opacity", "0.12") )
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("language", "ru") )
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("text_brightness", "0.8") )
            # Ensure votes table exists (updated for nominees)
            # Drop and recreate votes table to fix any old constraints
            db.execute("DROP TABLE IF EXISTS votes")
            db.execute("""
                CREATE TABLE votes (
                    ip TEXT,
                    nominee_id INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    voter_id TEXT,
                    nomination_id INTEGER,
                    user_agent TEXT,
                    nonce TEXT
                )
            """)
            # Add indexes
            db.execute("CREATE INDEX IF NOT EXISTS idx_votes_nominee_id ON votes(nominee_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_nominees_nomination_id ON nominees(nomination_id)")
            # Drop old index if exists and create new unique index
            db.execute("DROP INDEX IF EXISTS idx_votes_voter_nomination")
            db.execute("DROP INDEX IF EXISTS idx_votes_voter")
            db.execute("CREATE UNIQUE INDEX idx_votes_ip_ua_nomination_nonce ON votes(ip, user_agent, nomination_id, nonce)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_votes_ip_nomination ON votes(ip, nomination_id)")
                
            # Создаем триггер для обновления поля updated_at
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS update_nominations_timestamp
                AFTER UPDATE ON nominations
                FOR EACH ROW
                BEGIN
                    UPDATE nominations SET updated_at = CURRENT_TIMESTAMP
                    WHERE id = OLD.id;
                END;
            """)
 
# Flask 3.x убрал `before_first_request`; инициализируем базу данных прямо сейчас в контексте приложения
with app.app_context():
    init_db()


def get_settings():
    """Получение настроек из базы данных с кэшированием"""
    if not hasattr(get_settings, 'cache') or getattr(get_settings, 'last_updated', 0) < time.time() - 300:  # Кэш на 5 минут
        try:
            with get_db() as db:
                settings = {r['key']: r['value'] for r in db.execute("SELECT key, value FROM settings").fetchall()}
            get_settings.cache = settings
            get_settings.last_updated = time.time()
        except Exception as e:
            logger.error(f"Ошибка при загрузке настроек: {e}")
            get_settings.cache = {}
    return get_settings.cache

def clear_settings_cache():
    """Очистка кэша настроек"""
    if hasattr(get_settings, 'cache'):
        delattr(get_settings, 'cache')
    if hasattr(get_settings, 'last_updated'):
        delattr(get_settings, 'last_updated')

@app.context_processor
def inject_settings():
    """Добавление настроек в контекст шаблонов"""
    settings = get_settings()
    # If user has a theme cookie, prefer it for rendering (per-user choice)
    try:
        cookie_theme = request.cookies.get('theme')
        if cookie_theme in ('light', 'dark'):
            settings['theme'] = cookie_theme
    except Exception:
        pass
    # Default to dark theme for the site if not explicitly set
    if 'theme' not in settings or settings.get('theme') not in ('light', 'dark'):
        settings['theme'] = 'dark'
    # Respect environment flag to disable confetti/celebrations if desired
    try:
        import os as _os
        settings['disable_confetti'] = '1' if _os.getenv('DISABLE_CONFETTI', '').lower() in ('1','true','yes') else '0'
    except Exception:
        settings['disable_confetti'] = '0'
    # Server-side sanitization / types coercion to avoid broken templates
    try:
        # candidates_opacity: store as string in DB but use float here
        co = settings.get('candidates_opacity')
        if co is None:
            settings['candidates_opacity'] = '0.12'
        else:
            try:
                fv = float(co)
                fv = max(0.02, min(0.6, fv))
                settings['candidates_opacity'] = str(fv)
            except Exception:
                settings['candidates_opacity'] = '0.12'
        # navbar_style: ensure allowed values
        ns = settings.get('navbar_style', 'transparent')
        if ns not in ('transparent', 'semi', 'blur'):
            settings['navbar_style'] = 'transparent'
        # use_background: normalize to '0' or '1'
        ub = settings.get('use_background', '0')
        settings['use_background'] = '1' if str(ub) in ('1', 'true', 'True') else '0'
    except Exception:
        # be defensive: fallback defaults
        settings.setdefault('candidates_opacity', '0.12')
        settings.setdefault('navbar_style', 'transparent')
        settings.setdefault('use_background', '0')
    # small translations map
    translations = {
        'en': {
            'home': 'Home',
            'admin': 'Admin',
            'logout': 'Logout',
            'login': 'Login',
            'site_title': settings.get('site_title', '')
        },
        'ru': {
            'home': 'Главная',
            'admin': 'Админка',
            'logout': 'Выйти',
            'login': 'Войти',
            'site_title': settings.get('site_title', '')
        }
    }
    lang = settings.get('language', 'ru') if settings else 'ru'
    def tr(key):
        return translations.get(lang, translations['ru']).get(key, key)
    return dict(settings=settings, tr=tr, csrf_token=generate_csrf)


@app.route('/user_theme', methods=['POST'])
def user_theme():
    # Allows client-side JS to set a cookie-based theme preference for the user
    from flask import jsonify
    data = request.get_json(silent=True) or {}
    theme = data.get('theme')
    if theme not in ('light', 'dark'):
        return jsonify({'ok': False, 'error': 'invalid theme'}), 400
    resp = jsonify({'ok': True, 'theme': theme})
    resp.set_cookie('theme', theme, max_age=60*60*24*365)
    return resp


# Note: no fixed upload size limit configured; uploaded images are resized instead of being rejected.

@app.route('/')
def index():
    """Главная страница со списком номинаций"""
    try:
        with get_db() as db:
            nominations = db.execute("""
                SELECT n.id, n.name, n.description, n.created_at, n.updated_at, COUNT(nm.id) as nominee_count
                FROM nominations n
                LEFT JOIN nominees nm ON n.id = nm.nomination_id
                GROUP BY n.id
                ORDER BY n.name
            """).fetchall()

            # Добавляем случайный порядок для отображения номинаций
            nominations = list(nominations)
            random.shuffle(nominations)

            # Проверяем, голосовал ли пользователь в каждой номинации
            voter_id = request.cookies.get('voter_id')
            nomination_vote_status = {}
            if voter_id:
                for nomination in nominations:
                    # Проверяем, есть ли голос от этого пользователя в этой номинации
                    vote = db.execute("SELECT 1 FROM votes WHERE voter_id = ? AND nomination_id = ?", (voter_id, nomination['id'])).fetchone()
                    nomination_vote_status[nomination['id']] = bool(vote)

            return render_template('index.html',
                               nominations=nominations,
                               nomination_vote_status=nomination_vote_status,
                               now=datetime.now())

    except Exception as e:
        logger.error(f"Ошибка при загрузке номинаций: {e}")
        flash('Произошла ошибка при загрузке номинаций. Пожалуйста, попробуйте позже.', 'danger')
        return render_template('index.html', nominations=[], nomination_vote_status={}, settings=get_settings())

@app.route("/vote/<int:nominee_id>", methods=["POST"])
@limiter.limit("10 per minute")
def vote(nominee_id):
    voter_id = request.cookies.get('voter_id')
    vote_token = request.form.get('vote_token')
    user_agent = request.headers.get('User-Agent', 'Unknown')
    ip = request.remote_addr
    if not voter_id:
        voter_id = secrets.token_hex(16)

    # Track a user-facing message & category so we can return it in JSON for AJAX clients
    msg = None
    category = 'info'
    ok = False
    with get_db() as db:
        # Get the nomination_id for the nominee being voted for
        nominee_info = db.execute("SELECT nomination_id FROM nominees WHERE id = ?", (nominee_id,)).fetchone()
        if not nominee_info:
            msg = "Номинант не найден."
            category = 'danger'
            ok = False
        else:
            nomination_id = nominee_info['nomination_id']

            # Check vote_token validity
            expected_token = session.get(f'vote_token_{nomination_id}')
            if not vote_token or vote_token != expected_token:
                msg = "Недействительный токен голосования."
                category = 'danger'
                ok = False
            else:
                # Check min timeout: 30s between votes per IP
                last_vote_time = db.execute("SELECT MAX(timestamp) FROM votes WHERE ip = ?", (ip,)).fetchone()[0]
                if last_vote_time:
                    last_vote_dt = datetime.fromisoformat(last_vote_time)
                    if (datetime.now() - last_vote_dt).total_seconds() < 30:
                        msg = "Слишком частые голосования. Подождите 30 секунд."
                        category = 'warning'
                        ok = False
                    else:
                        ok = True
                else:
                    ok = True

                # Anomaly detection: >5 votes per IP in 1min
                one_min_ago = (datetime.now() - timedelta(minutes=1)).isoformat()
                recent_votes = db.execute("SELECT COUNT(*) FROM votes WHERE ip = ? AND timestamp > ?", (ip, one_min_ago)).fetchone()[0]
                if recent_votes >= 5:
                    msg = "Обнаружена подозрительная активность. Голосование заблокировано."
                    category = 'danger'
                    ok = False

                if ok:
                    # Check if this IP+UA+nonce already has a vote in this nomination
                    nonce = session.get(f'nonce_{nomination_id}')
                    existing = db.execute("SELECT nominee_id FROM votes WHERE ip = ? AND user_agent = ? AND nomination_id = ? AND nonce = ?", (ip, user_agent, nomination_id, nonce)).fetchone()

                    if existing:
                        old_nid = existing['nominee_id']
                        if old_nid == nominee_id:
                            msg = "Вы уже голосовали за этого номинанта."
                            category = 'warning'
                            ok = False
                        else:
                            # Change vote within the same nomination: decrement old nominee, update vote, increment new nominee
                            db.execute("UPDATE nominees SET votes = CASE WHEN votes > 0 THEN votes - 1 ELSE 0 END WHERE id = ?", (old_nid,))
                            db.execute("UPDATE votes SET nominee_id = ?, timestamp = CURRENT_TIMESTAMP WHERE ip = ? AND user_agent = ? AND nomination_id = ? AND nonce = ?", (nominee_id, ip, user_agent, nomination_id, nonce))
                            db.execute("UPDATE nominees SET votes = votes + 1 WHERE id = ?", (nominee_id,))
                            msg = "Ваш голос изменён."
                            category = 'success'
                            ok = True

                            # Логирование изменения голосования
                            nominee_info = db.execute("SELECT name FROM nominees WHERE id = ?", (nominee_id,)).fetchone()
                            nominee_name = nominee_info['name'] if nominee_info else 'Unknown'
                            vote_logger.info(f"Vote Change: Nominee={nominee_name}, IP={ip}, Browser/Device={user_agent}")
                            # Clear the used token
                            session.pop(f'vote_token_{nomination_id}', None)
                            session.pop(f'nonce_{nomination_id}', None)
                    else:
                        # New vote: insert
                        db.execute("UPDATE nominees SET votes = votes + 1 WHERE id = ?", (nominee_id,))
                        db.execute("INSERT INTO votes (ip, nominee_id, voter_id, nomination_id, user_agent, nonce) VALUES (?, ?, ?, ?, ?, ?)", (ip, nominee_id, voter_id, nomination_id, user_agent, nonce))
                        msg = "Ваш голос учтён!"
                        category = 'success'
                        ok = True

                        # Логирование голосования
                        nominee_info = db.execute("SELECT name FROM nominees WHERE id = ?", (nominee_id,)).fetchone()
                        nominee_name = nominee_info['name'] if nominee_info else 'Unknown'
                        vote_logger.info(f"Vote: Nominee={nominee_name}, IP={ip}, Browser/Device={user_agent}")
                        # Clear the used token
                        session.pop(f'vote_token_{nomination_id}', None)
                        session.pop(f'nonce_{nomination_id}', None)
    # If this looks like an XHR/fetch request, return JSON with updated counts
    # POST requests from forms are never treated as AJAX
    ajax = request.method == 'GET' and request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    # Force redirect for POST requests (form submissions)
    if request.method == 'POST':
        ajax = False
    # Build response payload
    with get_db() as db:
        nominees = db.execute("SELECT id, name, votes FROM nominees WHERE id = ? ORDER BY votes DESC", (nominee_id,)).fetchall()
        nominees_list = [{'id': r['id'], 'name': r['name'], 'votes': r['votes']} for r in nominees]
        my_vote = None
        vr = db.execute("SELECT nominee_id FROM votes WHERE voter_id = ? AND nomination_id = ?", (voter_id, nomination_id)).fetchone()
        if vr:
            my_vote = vr['nominee_id']
    # For non-AJAX flows, keep using flash messages for compatibility
    if not ajax:
        try:
            flash(msg or 'Действие выполнено.', category or 'info')
        except Exception:
            pass

    if ajax:
        from flask import jsonify
        resp = jsonify({'ok': bool(ok), 'message': msg or 'Действие выполнено.', 'category': category, 'nominees': nominees_list, 'my_vote': my_vote, 'focus': f'nominee-{nominee_id}'})
        resp.set_cookie('voter_id', voter_id, max_age=60*60*24*365, httponly=True, samesite='Lax')
        return resp
    # fallback for normal form submit: redirect back to index with focus param
    redirect_url = url_for("index") + f"?focus=nominee-{nominee_id}"
    resp = make_response(redirect(redirect_url))
    resp.set_cookie('voter_id', voter_id, max_age=60*60*24*365, httponly=True, samesite='Lax')
    return resp
    


@app.route('/nomination/<int:nomination_id>')
def nomination_detail(nomination_id):
    try:
        with get_db() as db:
            nomination = db.execute("SELECT id, name, description, created_at, updated_at FROM nominations WHERE id = ?", (nomination_id,)).fetchone()
            if not nomination:
                flash('Номинация не найдена', 'danger')
                return redirect(url_for('index'))

            nominees = db.execute("SELECT * FROM nominees WHERE nomination_id = ? ORDER BY votes DESC", (nomination_id,)).fetchall()
            voter_id = request.cookies.get('voter_id')
            my_vote = None
            if voter_id:
                vr = db.execute("SELECT nominee_id FROM votes WHERE voter_id = ? AND nomination_id = ?", (voter_id, nomination_id)).fetchone()
                if vr:
                    my_vote = vr['nominee_id']
            # Generate vote_token for this nomination page load
            vote_token = secrets.token_hex(16)
            nonce = str(int(time.time()))  # timestamp as nonce
            session[f'vote_token_{nomination_id}'] = vote_token
            session[f'nonce_{nomination_id}'] = nonce
        return render_template("nomination.html", nomination=nomination, nominees=nominees, my_vote=my_vote, vote_token=vote_token)
    except Exception as e:
        app.logger.exception('Error rendering nomination: %s', e)
        flash('Произошла ошибка при отображении номинации.', 'danger')
        return redirect(url_for('index'))

@app.route('/unvote/<int:nominee_id>', methods=['POST'])
def unvote(nominee_id):
    voter_id = request.cookies.get('voter_id')
    vote_token = request.form.get('vote_token')
    user_agent = request.headers.get('User-Agent', 'Unknown')
    ip = request.remote_addr

    msg = None
    category = 'info'
    ok = False
    if not voter_id:
        msg = 'Вы ещё не голосовали.'
        category = 'warning'
        ok = False
        # For non-AJAX redirect immediately
        if not (request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json or 'application/json' in request.accept_mimetypes):
            flash(msg, category)
            return redirect(url_for('index'))
    else:
        with get_db() as db:
            # Get the nomination_id for the nominee being unvoted
            nominee_info = db.execute("SELECT nomination_id FROM nominees WHERE id = ?", (nominee_id,)).fetchone()
            if not nominee_info:
                msg = "Номинант не найден."
                category = 'danger'
                ok = False
            else:
                nomination_id = nominee_info['nomination_id']

                # Check vote_token validity
                expected_token = session.get(f'vote_token_{nomination_id}')
                if not vote_token or vote_token != expected_token:
                    msg = "Недействительный токен голосования."
                    category = 'danger'
                    ok = False
                else:
                    # Check min timeout: 30s between votes per IP
                    last_vote_time = db.execute("SELECT MAX(timestamp) FROM votes WHERE ip = ?", (ip,)).fetchone()[0]
                    if last_vote_time:
                        last_vote_dt = datetime.fromisoformat(last_vote_time)
                        if (datetime.now() - last_vote_dt).total_seconds() < 30:
                            msg = "Слишком частые голосования. Подождите 30 секунд."
                            category = 'warning'
                            ok = False
                        else:
                            ok = True
                    else:
                        ok = True

                    if ok:
                        row = db.execute('SELECT nominee_id FROM votes WHERE ip = ? AND user_agent = ? AND nomination_id = ? AND nonce = ?', (ip, user_agent, nomination_id, session.get(f'nonce_{nomination_id}'))).fetchone()
                        if not row:
                            msg = 'Вы ещё не голосовали в этой номинации.'
                            category = 'warning'
                            ok = False
                        else:
                            if row['nominee_id'] != nominee_id:
                                msg = 'Нельзя отменить голос за другого номинанта.'
                                category = 'warning'
                                ok = False
                            else:
                                db.execute('DELETE FROM votes WHERE ip = ? AND user_agent = ? AND nomination_id = ? AND nonce = ?', (ip, user_agent, nomination_id, session.get(f'nonce_{nomination_id}')))
                                db.execute('UPDATE nominees SET votes = CASE WHEN votes > 0 THEN votes - 1 ELSE 0 END WHERE id = ?', (nominee_id,))
                                msg = 'Ваш голос отменён.'
                                category = 'info'
                                ok = True

                                # Логирование отмены голосования
                                nominee_info = db.execute("SELECT name FROM nominees WHERE id = ?", (nominee_id,)).fetchone()
                                nominee_name = nominee_info['name'] if nominee_info else 'Unknown'
                                vote_logger.info(f"Unvote: Nominee={nominee_name}, IP={ip}, Browser/Device={user_agent}")

                                # Clear the used token
                                session.pop(f'vote_token_{nomination_id}', None)
                                session.pop(f'nonce_{nomination_id}', None)
    # Support AJAX responses
    ajax = request.method == 'GET' and request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    # Force redirect for POST requests (form submissions)
    if request.method == 'POST':
        ajax = False
    with get_db() as db:
        # Get nomination_id for the nominee
        nominee_info = db.execute("SELECT nomination_id FROM nominees WHERE id = ?", (nominee_id,)).fetchone()
        nomination_id = nominee_info['nomination_id'] if nominee_info else None
        nominees = db.execute("SELECT id, name, votes FROM nominees WHERE nomination_id = ? ORDER BY votes DESC", (nomination_id,)).fetchall()
        nominees_list = [{'id': r['id'], 'name': r['name'], 'votes': r['votes']} for r in nominees]
        my_vote = None
        if nomination_id:
            vr = db.execute("SELECT nominee_id FROM votes WHERE voter_id = ? AND nomination_id = ?", (voter_id, nomination_id)).fetchone()
            if vr:
                my_vote = vr['nominee_id']
    # For non-AJAX flows, flash the message so server-rendered toasts appear
    if not ajax:
        try:
            flash(msg or 'Действие выполнено.', category or 'info')
        except Exception:
            pass

    if ajax:
        from flask import jsonify
        resp = jsonify({'ok': bool(ok), 'message': msg or 'Действие выполнено.', 'category': category, 'nominees': nominees_list, 'my_vote': my_vote, 'focus': f'nominee-{nominee_id}'})
        resp.set_cookie('voter_id', voter_id, max_age=60*60*24*365, httponly=True, samesite='Lax')
        return resp
    redirect_url = url_for('index') + f"?focus=nominee-{nominee_id}"
    resp = make_response(redirect(redirect_url))
    resp.set_cookie('voter_id', voter_id, max_age=60*60*24*365, httponly=True, samesite='Lax')
    return resp
    

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        logger.info(f"Login attempt: username={username}, password provided")
        if username == ADMIN_USERNAME and bcrypt.checkpw(password.encode('utf-8'), ADMIN_PASSWORD_HASH.encode('utf-8')):
            session["admin_logged_in"] = True
            return redirect(url_for("admin_panel"))
        else:
            logger.info(f"Login failed: expected username={ADMIN_USERNAME}")
            flash("Неверный логин или пароль", "danger")
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("index"))

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


@app.route('/admin/reset_settings', methods=['POST'])
@admin_required
def admin_reset_settings():
    # reset potentially dangerous settings to safe defaults (admin-only)
    try:
        set_setting('candidates_opacity', '0.12')
        set_setting('navbar_style', 'transparent')
        set_setting('background_image', '')
        set_setting('use_background', '0')
        flash('Настройки успешно сброшены до безопасных значений.', 'success')
    except Exception as e:
        app.logger.exception('Error resetting settings: %s', e)
        flash('Не удалось сбросить настройки.', 'danger')
    return redirect(url_for('admin_settings'))

@app.route("/admin")
@admin_required
def admin_panel():
    with get_db() as db:
        nominations = db.execute("SELECT * FROM nominations ORDER BY id").fetchall()
        nominees = db.execute("SELECT * FROM nominees ORDER BY nomination_id, id").fetchall()
        settings = {r['key']: r['value'] for r in db.execute("SELECT key, value FROM settings").fetchall()}
    return render_template("admin.html", nominations=nominations, nominees=nominees, settings=settings)


def get_setting(key, default=None):
    with get_db() as db:
        cur = db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row['value'] if row else default


def set_setting(key, value):
    with get_db() as db:
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    if request.method == 'POST':
        try:
            title = request.form.get('site_title', '').strip()
            banner = request.form.get('banner_text', '').strip()
            color = request.form.get('primary_color', '').strip() or '#0d6efd'
            header_file = request.files.get('header_image')
            bg_file = request.files.get('background_image')
            use_bg = '1' if request.form.get('use_background') == 'on' else '0'
            theme_choice = request.form.get('theme', 'light')
            navbar_style = request.form.get('navbar_style', 'transparent')
            candidates_opacity = request.form.get('candidates_opacity', '').strip() or None
            text_brightness = request.form.get('text_brightness', '').strip() or None
            language = request.form.get('language', 'ru')
            # validate navbar_style
            if navbar_style not in ('transparent', 'semi', 'blur'):
                navbar_style = 'transparent'
            header_filename = get_setting('header_image', '')
            background_filename = get_setting('background_image', '')
            if header_file and header_file.filename:
                fn = secure_filename(header_file.filename)
                ext = fn.rsplit('.', 1)[-1].lower() if '.' in fn else ''
                if ext in ALLOWED_EXTENSIONS:
                    unique = secrets.token_hex(8)
                    header_filename = f"header_{unique}.{ext}"
                    header_file.save(os.path.join(UPLOAD_FOLDER, header_filename))
                    # try to resize header to reasonable size
                    try:
                        with Image.open(os.path.join(UPLOAD_FOLDER, header_filename)) as img:
                            img.thumbnail((2400,800))
                            img.save(os.path.join(UPLOAD_FOLDER, header_filename), optimize=True, quality=80)
                    except Exception:
                        pass
                else:
                    flash('Недопустимый формат изображения заголовка', 'danger')
                    return redirect(url_for('admin_settings'))
            if bg_file and bg_file.filename:
                fn = secure_filename(bg_file.filename)
                ext = fn.rsplit('.', 1)[-1].lower() if '.' in fn else ''
                if ext in ALLOWED_EXTENSIONS:
                    unique = secrets.token_hex(8)
                    background_filename = f"background_{unique}.{ext}"
                    bg_file.save(os.path.join(UPLOAD_FOLDER, background_filename))
                    try:
                        with Image.open(os.path.join(UPLOAD_FOLDER, background_filename)) as img:
                            img.thumbnail((3000,2000))
                            img.save(os.path.join(UPLOAD_FOLDER, background_filename), optimize=True, quality=80)
                    except Exception:
                        pass
                else:
                    flash('Недопустимый формат фонового изображения', 'danger')
                    return redirect(url_for('admin_settings'))
            set_setting('site_title', title)
            set_setting('banner_text', banner)
            set_setting('primary_color', color)
            set_setting('language', language)
            set_setting('header_image', header_filename)
            set_setting('background_image', background_filename)
            set_setting('use_background', use_bg)
            set_setting('navbar_style', navbar_style)
            if candidates_opacity is not None:
                # sanitize numeric value
                try:
                    v = float(candidates_opacity)
                    # clamp 0.02 .. 0.6
                    v = max(0.02, min(0.6, v))
                    set_setting('candidates_opacity', str(v))
                except Exception:
                    flash('Недопустимое значение прозрачности — сохранено предыдущее.', 'warning')
            if text_brightness is not None:
                # sanitize numeric value
                try:
                    v = float(text_brightness)
                    # clamp 0.1 .. 1.0
                    v = max(0.1, min(1.0, v))
                    set_setting('text_brightness', str(v))
                except Exception:
                    flash('Недопустимое значение яркости текста — сохранено предыдущее.', 'warning')
            set_setting('theme', 'dark')  # Always set to dark
            clear_settings_cache()  # Очистка кэша настроек для немедленного применения изменений
            flash('Настройки сохранены', 'success')
            return redirect(url_for('admin_settings'))
        except Exception as e:
            app.logger.exception('Error saving admin settings: %s', e)
            flash('Ошибка при сохранении настроек — проверьте введённые значения.', 'danger')
            return redirect(url_for('admin_settings'))

    # GET
    with get_db() as db:
        settings = {r['key']: r['value'] for r in db.execute("SELECT key, value FROM settings").fetchall()}
    return render_template('admin_settings.html', settings=settings)


@app.route('/admin/edit/<int:nominee_id>', methods=['GET', 'POST'])
@admin_required
def admin_edit(nominee_id):
    with get_db() as db:
        cur = db.execute('SELECT * FROM nominees WHERE id = ?', (nominee_id,))
        nominee = cur.fetchone()
        if not nominee:
            flash('Номинант не найден', 'danger')
            return redirect(url_for('admin_panel'))

    if request.method == 'POST':
        name = sanitize_text(request.form.get('name', '').strip())
        category = sanitize_text(request.form.get('category', '').strip())
        desc = sanitize_html(request.form.get('description', '').strip())
        photo_file = request.files.get('photo')
        photo_filename = nominee['photo']
        if photo_file and photo_file.filename:
            fn = secure_filename(photo_file.filename)
            ext = fn.rsplit('.', 1)[-1].lower() if '.' in fn else ''
            if ext in ALLOWED_EXTENSIONS:
                unique = secrets.token_hex(8)
                newfn = f"{unique}.{ext}"
                photo_file.save(os.path.join(UPLOAD_FOLDER, newfn))
                # resize uploaded edit photo
                try:
                    with Image.open(os.path.join(UPLOAD_FOLDER, newfn)) as img:
                        img.thumbnail((1600,1600))
                        img.save(os.path.join(UPLOAD_FOLDER, newfn), optimize=True, quality=85)
                except Exception:
                    pass
                # remove old
                if photo_filename:
                    try:
                        os.remove(os.path.join(UPLOAD_FOLDER, photo_filename))
                    except Exception:
                        pass
                photo_filename = newfn
            else:
                flash('Недопустимый формат изображения.', 'danger')
                return redirect(url_for('admin_edit', nominee_id=nominee_id))
        with get_db() as db:
            db.execute('UPDATE nominees SET name=?, category=?, description=?, photo=? WHERE id=?', (name, category, desc, photo_filename, nominee_id))
        flash('Номинант обновлён', 'success')
        return redirect(url_for('admin_panel'))

    return render_template('admin_edit.html', nominee=nominee)

@app.route("/admin/add", methods=["POST"])
@admin_required
def admin_add():
    name = request.form.get("name").strip()
    category = request.form.get("category", "").strip()
    desc = request.form.get("description", "").strip()
    nomination_id = request.form.get("nomination_id")
    photo_file = request.files.get('photo')
    photo_filename = None
    if photo_file and photo_file.filename:
        filename = secure_filename(photo_file.filename)
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        if ext in ALLOWED_EXTENSIONS:
            unique = secrets.token_hex(8)
            photo_filename = f"{unique}.{ext}"
            save_path = os.path.join(UPLOAD_FOLDER, photo_filename)
            photo_file.save(save_path)
        else:
            flash("Недопустимый формат изображения. Разрешены: png, jpg, jpeg, gif.", "danger")
            return redirect(url_for('admin_panel'))
    if name and nomination_id:
        # if photo exists, try to resize/optimize
        if photo_filename:
            try:
                img_path = os.path.join(UPLOAD_FOLDER, photo_filename)
                with Image.open(img_path) as img:
                    img.thumbnail((1200, 1200))
                    img.save(img_path, optimize=True, quality=85)
            except Exception:
                flash('Не удалось обработать изображение, сохранено исходное.', 'warning')

        with get_db() as db:
            db.execute(
                "INSERT INTO nominees (name, category, description, photo, nomination_id) VALUES (?, ?, ?, ?, ?)",
                (name, category, desc, photo_filename, nomination_id)
            )
        flash("Номинант добавлен", "success")
    else:
        flash("Имя и номинация обязательны", "danger")
    return redirect(url_for("admin_panel"))

@app.route("/admin/add_nomination", methods=["POST"])
@admin_required
def admin_add_nomination():
    name = sanitize_text(request.form.get("name", "").strip())
    description = sanitize_html(request.form.get("description", "").strip())
    if name:
        with get_db() as db:
            db.execute(
                "INSERT INTO nominations (name, description) VALUES (?, ?)",
                (name, description)
            )
        flash("Номинация добавлена", "success")
    else:
        flash("Название номинации обязательно", "danger")
    return redirect(url_for("admin_panel"))

@app.route('/admin/edit_nomination/<int:nomination_id>', methods=['GET', 'POST'])
@admin_required
def admin_edit_nomination(nomination_id):
    with get_db() as db:
        cur = db.execute('SELECT * FROM nominations WHERE id = ?', (nomination_id,))
        nomination = cur.fetchone()
        if not nomination:
            flash('Номинация не найдена', 'danger')
            return redirect(url_for('admin_panel'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        if name:
            with get_db() as db:
                db.execute('UPDATE nominations SET name=?, description=? WHERE id=?', (name, description, nomination_id))
            flash('Номинация обновлена', 'success')
            return redirect(url_for('admin_panel'))
        else:
            flash('Название номинации обязательно', 'danger')

    return render_template('admin_edit_nomination.html', nomination=nomination)

@app.route("/admin/delete_nomination/<int:nomination_id>")
@admin_required
def admin_delete_nomination(nomination_id):
    with get_db() as db:
        # First delete all nominees in this nomination and their votes
        nominees_cur = db.execute("SELECT id, photo FROM nominees WHERE nomination_id = ?", (nomination_id,))
        nominees = nominees_cur.fetchall()
        for nominee in nominees:
            nominee_id = nominee['id']
            photo = nominee['photo']
            # remove uploaded photo file if exists
            if photo:
                try:
                    os.remove(os.path.join(UPLOAD_FOLDER, photo))
                except Exception:
                    pass
            # delete votes for this nominee
            db.execute("DELETE FROM votes WHERE nominee_id = ?", (nominee_id,))
        # delete nominees
        db.execute("DELETE FROM nominees WHERE nomination_id = ?", (nomination_id,))
        # delete nomination itself
        db.execute("DELETE FROM nominations WHERE id = ?", (nomination_id,))
    flash("Номинация и все номинанты в ней удалены", "info")
    return redirect(url_for("admin_panel"))

@app.route("/admin/delete/<int:nominee_id>")
@admin_required
def admin_delete(nominee_id):
    with get_db() as db:
        # remove uploaded photo file if exists
        cur = db.execute("SELECT photo FROM nominees WHERE id = ?", (nominee_id,))
        row = cur.fetchone()
        if row and row['photo']:
            try:
                os.remove(os.path.join(UPLOAD_FOLDER, row['photo']))
            except Exception:
                pass
        db.execute("DELETE FROM nominees WHERE id = ?", (nominee_id,))
        db.execute("DELETE FROM votes WHERE nominee_id = ?", (nominee_id,))
    flash("Номинант удалён", "info")
    return redirect(url_for("admin_panel"))

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
