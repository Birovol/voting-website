from flask import Flask, render_template, request, redirect, url_for, flash, session, make_response
import sqlite3
import os
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
import secrets
from PIL import Image
import sqlite3

# Настройки
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD_HASH = generate_password_hash("secret")

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config["DATABASE"] = os.path.join(app.instance_path, "site.db")
# No MAX_CONTENT_LENGTH set (uploads unrestricted). Images will still be resized on upload.
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
UPLOAD_FOLDER = os.path.join(app.static_folder or 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

os.makedirs(app.instance_path, exist_ok=True)

def get_db():
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS teachers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                subject TEXT,
                description TEXT,
                votes INTEGER DEFAULT 0
            )
        """)
        # Seed some sample teachers if table is empty
        cur = db.execute("SELECT COUNT(1) as cnt FROM teachers")
        cnt = cur.fetchone()[0]
        if cnt == 0:
            db.executemany(
                "INSERT INTO teachers (name, subject, description, votes) VALUES (?, ?, ?, ?)",
                [
                    ("Иванов Иван", "Математика", "Опытный преподаватель высшей категории.", 10),
                    ("Петрова Мария", "Русский язык", "Любит творчество и проекты с учениками.", 7),
                    ("Сидоров Алексей", "Физика", "Провёл несколько олимпиадных курсов.", 5),
                ],
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
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("site_title", "Выбор Учителя Года"))
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("banner_text", "Выпускной 2025 — поздравляем!") )
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("primary_color", "#0d6efd") )
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("header_image", "") )
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("background_image", "") )
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("use_background", "0") )
            # Make dark theme the default
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("theme", "dark") )
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("navbar_style", "transparent") )
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("candidates_opacity", "0.12") )
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("language", "ru") )
        # Ensure votes table exists
        db.execute("""
            CREATE TABLE IF NOT EXISTS votes (
                ip TEXT,
                teacher_id INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(ip, teacher_id)
            )
        """)
        # If votes table was created with UNIQUE(ip, teacher_id), remove that uniqueness constraint
        cursql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='votes'").fetchone()
        if cursql and 'UNIQUE(ip, teacher_id)' in (cursql[0] or ''):
            # recreate table without UNIQUE constraint (SQLite: recreate table and copy data)
            db.execute("ALTER TABLE votes RENAME TO votes_old")
            db.execute("CREATE TABLE votes (ip TEXT, teacher_id INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, voter_id TEXT)")
            db.execute("INSERT INTO votes (ip, teacher_id, timestamp, voter_id) SELECT ip, teacher_id, timestamp, voter_id FROM votes_old")
            db.execute("DROP TABLE votes_old")
        # Add voter_id column to votes if missing, then create unique index on it
        curv = db.execute("PRAGMA table_info(votes)")
        vcols = [r['name'] for r in curv.fetchall()]
        if 'voter_id' not in vcols:
            db.execute("ALTER TABLE votes ADD COLUMN voter_id TEXT")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_votes_voter_id ON votes(voter_id)")

        # Add photo column to teachers table if missing (SQLite: ALTER TABLE ADD COLUMN)
        cur = db.execute("PRAGMA table_info(teachers)")
        cols = [r['name'] for r in cur.fetchall()]
        if 'photo' not in cols:
            db.execute("ALTER TABLE teachers ADD COLUMN photo TEXT")

# Flask 3.x убрал `before_first_request`; инициализируем базу данных прямо сейчас в контексте приложения
with app.app_context():
    init_db()


@app.context_processor
def inject_settings():
    try:
        with get_db() as db:
            settings = {r['key']: r['value'] for r in db.execute("SELECT key, value FROM settings").fetchall()}
    except Exception:
        settings = {}
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
    return dict(settings=settings, tr=tr)


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

@app.route("/")
def index():
    try:
        with get_db() as db:
            teachers = db.execute("SELECT * FROM teachers ORDER BY votes DESC").fetchall()
            settings = {r['key']: r['value'] for r in db.execute("SELECT key, value FROM settings").fetchall()}
            voter_id = request.cookies.get('voter_id')
            my_vote = None
            if voter_id:
                vr = db.execute("SELECT teacher_id FROM votes WHERE voter_id = ?", (voter_id,)).fetchone()
                if vr:
                    my_vote = vr['teacher_id']
        return render_template("index.html", teachers=teachers, settings=settings, my_vote=my_vote)
    except Exception as e:
        app.logger.exception('Error rendering index: %s', e)
        # show a friendly page with an explanation and admin link
        flash('Произошла ошибка при отображении главной страницы — проверьте настройки в админке.', 'danger')
        return render_template('index.html', teachers=[], settings={}, my_vote=None)

@app.route("/vote/<int:teacher_id>", methods=["POST"])
def vote(teacher_id):
    ip = request.remote_addr
    voter_id = request.cookies.get('voter_id')
    if not voter_id:
        voter_id = secrets.token_hex(16)

    # Track a user-facing message & category so we can return it in JSON for AJAX clients
    msg = None
    category = 'info'
    ok = False
    with get_db() as db:
        # Check if this voter_id already has a vote (so we can change it)
        existing = db.execute("SELECT teacher_id FROM votes WHERE voter_id = ?", (voter_id,)).fetchone()
        if existing:
            old_tid = existing['teacher_id']
            if old_tid == teacher_id:
                msg = "Вы уже голосовали за этого учителя."
                category = 'warning'
                ok = False
            else:
                # perform change: decrement old teacher, update vote row, increment new teacher
                try:
                    db.execute("UPDATE teachers SET votes = CASE WHEN votes > 0 THEN votes - 1 ELSE 0 END WHERE id = ?", (old_tid,))
                    db.execute("UPDATE votes SET teacher_id = ?, ip = ?, timestamp = CURRENT_TIMESTAMP WHERE voter_id = ?", (teacher_id, ip, voter_id))
                    db.execute("UPDATE teachers SET votes = votes + 1 WHERE id = ?", (teacher_id,))
                    msg = "Ваш голос изменён."
                    category = 'success'
                    ok = True
                except sqlite3.IntegrityError:
                    # fallback: if update violated some constraint, warn the user
                    msg = "Не удалось сменить голос из-за конфликта; попробуйте позже."
                    category = 'warning'
                    ok = False
        else:
            # New voter: ensure IP hasn't voted for this teacher already
            ip_existing = db.execute("SELECT 1 FROM votes WHERE ip = ? AND teacher_id = ?", (ip, teacher_id)).fetchone()
            if ip_existing:
                msg = "С этого IP уже был голос за этого учителя."
                category = 'warning'
                ok = False
            else:
                try:
                    db.execute("UPDATE teachers SET votes = votes + 1 WHERE id = ?", (teacher_id,))
                    db.execute("INSERT INTO votes (ip, teacher_id, voter_id) VALUES (?, ?, ?)", (ip, teacher_id, voter_id))
                    msg = "Ваш голос учтён!"
                    category = 'success'
                    ok = True
                except sqlite3.IntegrityError:
                    msg = "Голос не был принят: вы уже голосовали."
                    category = 'warning'
                    ok = False
    # If this looks like an XHR/fetch request, return JSON with updated counts
    ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json or 'application/json' in request.accept_mimetypes
    # Build response payload
    with get_db() as db:
        teachers = db.execute("SELECT id, name, votes FROM teachers ORDER BY votes DESC").fetchall()
        teachers_list = [{'id': r['id'], 'name': r['name'], 'votes': r['votes']} for r in teachers]
        my_vote = None
        vr = db.execute("SELECT teacher_id FROM votes WHERE voter_id = ?", (voter_id,)).fetchone()
        if vr:
            my_vote = vr['teacher_id']
    # For non-AJAX flows, keep using flash messages for compatibility
    if not ajax:
        try:
            flash(msg or 'Действие выполнено.', category or 'info')
        except Exception:
            pass

    if ajax:
        from flask import jsonify
        resp = jsonify({'ok': bool(ok), 'message': msg or 'Действие выполнено.', 'category': category, 'teachers': teachers_list, 'my_vote': my_vote, 'focus': f'candidate-{teacher_id}'})
        resp.set_cookie('voter_id', voter_id, max_age=60*60*24*365, httponly=True, samesite='Lax')
        return resp
    # fallback for normal form submit: redirect back to index with focus param
    redirect_url = url_for("index") + f"?focus=candidate-{teacher_id}"
    resp = make_response(redirect(redirect_url))
    resp.set_cookie('voter_id', voter_id, max_age=60*60*24*365, httponly=True, samesite='Lax')
    return resp
    


@app.route('/unvote/<int:teacher_id>', methods=['POST'])
def unvote(teacher_id):
    voter_id = request.cookies.get('voter_id')
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
            row = db.execute('SELECT teacher_id FROM votes WHERE voter_id = ?', (voter_id,)).fetchone()
            if not row:
                msg = 'Вы ещё не голосовали.'
                category = 'warning'
                ok = False
            else:
                if row['teacher_id'] != teacher_id:
                    msg = 'Нельзя отменить голос за другого кандидата.'
                    category = 'warning'
                    ok = False
                else:
                    db.execute('DELETE FROM votes WHERE voter_id = ?', (voter_id,))
                    db.execute('UPDATE teachers SET votes = CASE WHEN votes > 0 THEN votes - 1 ELSE 0 END WHERE id = ?', (teacher_id,))
                    msg = 'Ваш голос отменён.'
                    category = 'info'
                    ok = True
    # Support AJAX responses
    ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json or 'application/json' in request.accept_mimetypes
    with get_db() as db:
        teachers = db.execute("SELECT id, name, votes FROM teachers ORDER BY votes DESC").fetchall()
        teachers_list = [{'id': r['id'], 'name': r['name'], 'votes': r['votes']} for r in teachers]
        my_vote = None
        vr = db.execute("SELECT teacher_id FROM votes WHERE voter_id = ?", (voter_id,)).fetchone()
        if vr:
            my_vote = vr['teacher_id']
    # For non-AJAX flows, flash the message so server-rendered toasts appear
    if not ajax:
        try:
            flash(msg or 'Действие выполнено.', category or 'info')
        except Exception:
            pass

    if ajax:
        from flask import jsonify
        resp = jsonify({'ok': bool(ok), 'message': msg or 'Действие выполнено.', 'category': category, 'teachers': teachers_list, 'my_vote': my_vote, 'focus': f'candidate-{teacher_id}'})
        resp.set_cookie('voter_id', voter_id, max_age=60*60*24*365, httponly=True, samesite='Lax')
        return resp
    redirect_url = url_for('index') + f"?focus=candidate-{teacher_id}"
    resp = make_response(redirect(redirect_url))
    resp.set_cookie('voter_id', voter_id, max_age=60*60*24*365, httponly=True, samesite='Lax')
    return resp
    

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username == ADMIN_USERNAME and check_password_hash(ADMIN_PASSWORD_HASH, password):
            session["admin_logged_in"] = True
            return redirect(url_for("admin_panel"))
        else:
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
        teachers = db.execute("SELECT * FROM teachers ORDER BY id").fetchall()
        settings = {r['key']: r['value'] for r in db.execute("SELECT key, value FROM settings").fetchall()}
    return render_template("admin.html", teachers=teachers, settings=settings)


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
            if theme_choice in ('light', 'dark'):
                set_setting('theme', theme_choice)
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


@app.route('/admin/edit/<int:teacher_id>', methods=['GET', 'POST'])
@admin_required
def admin_edit(teacher_id):
    with get_db() as db:
        cur = db.execute('SELECT * FROM teachers WHERE id = ?', (teacher_id,))
        t = cur.fetchone()
        if not t:
            flash('Учитель не найден', 'danger')
            return redirect(url_for('admin_panel'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        subject = request.form.get('subject', '').strip()
        desc = request.form.get('description', '').strip()
        photo_file = request.files.get('photo')
        photo_filename = t['photo']
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
                return redirect(url_for('admin_edit', teacher_id=teacher_id))
        with get_db() as db:
            db.execute('UPDATE teachers SET name=?, subject=?, description=?, photo=? WHERE id=?', (name, subject, desc, photo_filename, teacher_id))
        flash('Учитель обновлён', 'success')
        return redirect(url_for('admin_panel'))

    return render_template('admin_edit.html', t=t)

@app.route("/admin/add", methods=["POST"])
@admin_required
def admin_add():
    name = request.form.get("name").strip()
    subject = request.form.get("subject", "").strip()
    desc = request.form.get("description", "").strip()
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
    if name:
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
                "INSERT INTO teachers (name, subject, description, photo) VALUES (?, ?, ?, ?)",
                (name, subject, desc, photo_filename)
            )
        flash("Учитель добавлен", "success")
    else:
        flash("Имя обязательно", "danger")
    return redirect(url_for("admin_panel"))

@app.route("/admin/delete/<int:teacher_id>")
@admin_required
def admin_delete(teacher_id):
    with get_db() as db:
        # remove uploaded photo file if exists
        cur = db.execute("SELECT photo FROM teachers WHERE id = ?", (teacher_id,))
        row = cur.fetchone()
        if row and row['photo']:
            try:
                os.remove(os.path.join(UPLOAD_FOLDER, row['photo']))
            except Exception:
                pass
        db.execute("DELETE FROM teachers WHERE id = ?", (teacher_id,))
        db.execute("DELETE FROM votes WHERE teacher_id = ?", (teacher_id,))
    flash("Учитель удалён", "info")
    return redirect(url_for("admin_panel"))

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
