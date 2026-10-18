"""Олимп — локальная платформа. Python 3.10+, сторонние пакеты не нужны."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tasks import TASKS

ROOT = Path(__file__).resolve().parent


@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')

    try:
        with db:
            yield db
    finally:
        db.close()


def init_db(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    with connect(path) as db:
        db.execute('PRAGMA journal_mode = WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL COLLATE NOCASE UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (
                strftime('%Y-%m-%dT%H:%M:%fZ','now')
            )
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            subject TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            statement TEXT NOT NULL,
            answer TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            csrf TEXT NOT NULL,
            expires REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            task_id INTEGER NOT NULL REFERENCES tasks(id),
            answer TEXT NOT NULL,
            correct INTEGER NOT NULL CHECK(correct IN (0,1)),
            created_at TEXT NOT NULL DEFAULT (
                strftime('%Y-%m-%dT%H:%M:%fZ','now')
            )
        );

        CREATE TABLE IF NOT EXISTS solves (
            user_id INTEGER NOT NULL REFERENCES users(id),
            task_id INTEGER NOT NULL REFERENCES tasks(id),
            created_at TEXT NOT NULL DEFAULT (
                strftime('%Y-%m-%dT%H:%M:%fZ','now')
            ),
            PRIMARY KEY (user_id, task_id)
        );

        CREATE INDEX IF NOT EXISTS attempts_user
            ON attempts(user_id, id DESC);
        ''')

        task_columns = {
            row['name']
            for row in db.execute('PRAGMA table_info(tasks)')
        }

        if 'explanation' not in task_columns:
            db.execute(
                "ALTER TABLE tasks "
                "ADD COLUMN explanation TEXT NOT NULL DEFAULT ''"
            )

        db.execute('''
            CREATE TABLE IF NOT EXISTS reveals (
                user_id INTEGER NOT NULL REFERENCES users(id),
                task_id INTEGER NOT NULL REFERENCES tasks(id),
                created_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%fZ','now')
                ),
                PRIMARY KEY (user_id, task_id)
            )
        ''')

        columns = {
            row['name']
            for row in db.execute('PRAGMA table_info(reveals)')
        }

        if 'before_solve' not in columns:
            db.execute(
                'ALTER TABLE reveals '
                'ADD COLUMN before_solve INTEGER NOT NULL DEFAULT 1'
            )
            db.execute('''
                UPDATE reveals
                SET before_solve = NOT EXISTS (
                    SELECT 1 FROM solves s
                    WHERE s.user_id = reveals.user_id
                    AND s.task_id = reveals.task_id
                    AND s.created_at <= reveals.created_at
                )
            ''')

        if 'attempt_id' not in columns:
            db.execute(
                'ALTER TABLE reveals '
                'ADD COLUMN attempt_id INTEGER NOT NULL DEFAULT 0'
            )
            db.execute('''
                UPDATE reveals
                SET attempt_id = COALESCE((
                    SELECT MAX(a.id)
                    FROM attempts a
                    WHERE a.user_id = reveals.user_id
                    AND a.created_at <= reveals.created_at
                ), 0)
            ''')

        db.executemany('''
            INSERT INTO tasks(
                id, title, subject, difficulty,
                statement, answer, explanation
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                subject = excluded.subject,
                difficulty = excluded.difficulty,
                statement = excluded.statement,
                answer = excluded.answer,
                explanation = excluded.explanation
        ''', TASKS)


def points_for(db, uid):
    return db.execute('''
        SELECT COUNT(*)
        FROM solves s
        WHERE s.user_id = ?
        AND NOT EXISTS (
            SELECT 1 FROM reveals r
            WHERE r.user_id = s.user_id
            AND r.task_id = s.task_id
            AND r.before_solve = 1
        )
    ''', (uid,)).fetchone()[0]


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode(),
        bytes.fromhex(salt),
        600_000
    ).hex()
    return salt + ':' + digest


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class ApiError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


class Handler(BaseHTTPRequestHandler):
    server_version = 'Olymp/1.0'

    def log_message(self, fmt, *args):
        # Не записываем пароли, ответы и cookie в журнал.
        pass

    def send(
        self,
        status,
        data,
        content_type='application/json; charset=utf-8',
        cookie=None
    ):
        if content_type.startswith('application/json'):
            body = json.dumps(data, ensure_ascii=False).encode()
        else:
            body = data

        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header(
            'Content-Security-Policy',
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )

        if cookie:
            self.send_header('Set-Cookie', cookie)

        self.end_headers()
        self.wfile.write(body)

    def session(self, db):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            token = (
                cookie['olymp_session'].value
                if 'olymp_session' in cookie else ''
            )
        except Exception:
            token = ''

        return db.execute(
            'SELECT * FROM sessions WHERE token_hash = ? AND expires > ?',
            (token_hash(token), time.time())
        ).fetchone()

    def new_session(self, db, user_id=None, old=None):
        if old:
            db.execute(
                'DELETE FROM sessions WHERE token_hash = ?',
                (old['token_hash'],)
            )

        db.execute(
            'DELETE FROM sessions WHERE expires <= ?',
            (time.time(),)
        )

        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)

        db.execute(
            'INSERT INTO sessions VALUES (?, ?, ?, ?)',
            (token_hash(token), user_id, csrf, time.time() + 604800)
        )

        secure = '; Secure' if self.server.secure_cookie else ''
        cookie = (
            f'olymp_session={token}; Path=/; HttpOnly; '
            f'SameSite=Lax; Max-Age=604800{secure}'
        )
        return csrf, cookie

    def body(self, allowed):
        if self.headers.get_content_type() != 'application/json':
            raise ApiError(415, 'Используйте JSON.')

        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise ApiError(400, 'Некорректная длина запроса.')

        if not 0 < length <= 4096:
            raise ApiError(413, 'Недопустимый размер запроса.')

        try:
            data = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise ApiError(400, 'Некорректный JSON.')

        if not isinstance(data, dict) or set(data) != set(allowed):
            raise ApiError(400, 'Некорректные поля запроса.')

        try:
            for value in data.values():
                if isinstance(value, str):
                    value.encode('utf-8')
        except UnicodeError:
            raise ApiError(400, 'Некорректная кодировка текста.')

        return data

    def do_GET(self):
        self.handle_request(False)

    def do_POST(self):
        self.handle_request(True)

    def handle_request(self, post):
        try:
            with connect(self.server.db_path) as db:
                self.route(db, post)
        except ApiError as exc:
            self.send(exc.status, {'error': exc.message})
        except sqlite3.Error:
            self.send(503, {
                'error': 'База данных временно недоступна. Повторите запрос.'
            })
        except (BrokenPipeError, ConnectionResetError):
            pass

    def route(self, db, post):
        path = self.path.split('?', 1)[0]

        if not post and path in (
            '/',
            '/static/style.css',
            '/static/app.js'
        ):
            file = ROOT / (
                'static/index.html'
                if path == '/' else path.lstrip('/')
            )
            mime = {
                'html': 'text/html',
                'css': 'text/css',
                'js': 'text/javascript'
            }[file.suffix[1:]]

            return self.send(
                200,
                file.read_bytes(),
                mime + '; charset=utf-8'
            )

        session = self.session(db)

        if not post and path == '/api/session':
            cookie = None

            if not session:
                csrf, cookie = self.new_session(db)
            else:
                csrf = session['csrf']

            user = (
                db.execute(
                    'SELECT id, username FROM users WHERE id = ?',
                    (session['user_id'],)
                ).fetchone()
                if session else None
            )

            db.commit()
            return self.send(
                200,
                {'csrf': csrf, 'user': dict(user) if user else None},
                cookie=cookie
            )

        if post:
            origin = self.headers.get('Origin')
            host = self.headers.get('Host', '')

            if origin and origin not in (
                'http://' + host,
                'https://' + host
            ):
                raise ApiError(403, 'Недопустимый источник запроса.')

            supplied_csrf = self.headers.get(
                'X-CSRF-Token', ''
            ).encode('utf-8')

            if not session or not hmac.compare_digest(
                supplied_csrf,
                session['csrf'].encode('utf-8')
            ):
                raise ApiError(403, 'Сессия устарела. Обновите страницу.')

        if post and path in ('/api/register', '/api/login'):
            data = self.body(('username', 'password'))
            username = data['username']
            password = data['password']

            if (
                not isinstance(username, str)
                or not re.fullmatch(r'[A-Za-z0-9_]{3,32}', username)
            ):
                raise ApiError(
                    400,
                    'Логин: 3–32 латинские буквы, цифры или знак _.'
                )

            if (
                not isinstance(password, str)
                or not 8 <= len(password) <= 128
            ):
                raise ApiError(
                    400,
                    'Пароль должен содержать от 8 до 128 символов.'
                )

            if path == '/api/register':
                hashed = password_hash(password)

                try:
                    cursor = db.execute(
                        'INSERT INTO users(username, password_hash) '
                        'VALUES (?, ?)',
                        (username, hashed)
                    )
                except sqlite3.IntegrityError:
                    raise ApiError(409, 'Этот логин уже занят.')

                uid = cursor.lastrowid
            else:
                user = db.execute(
                    'SELECT * FROM users WHERE username = ?',
                    (username,)
                ).fetchone()

                stored = (
                    user['password_hash']
                    if user else '0' * 32 + ':' + '0' * 64
                )

                valid_password = hmac.compare_digest(
                    password_hash(password, stored.split(':')[0]),
                    stored
                )

                if not valid_password or not user:
                    raise ApiError(401, 'Неверный логин или пароль.')

                uid = user['id']
                username = user['username']

            csrf, cookie = self.new_session(db, uid, session)
            db.commit()

            return self.send(
                200,
                {
                    'user': {'id': uid, 'username': username},
                    'csrf': csrf
                },
                cookie=cookie
            )

        if not session or session['user_id'] is None:
            raise ApiError(401, 'Войдите в аккаунт.')

        uid = session['user_id']

        if post and path == '/api/logout':
            self.body(())
            csrf, cookie = self.new_session(db, old=session)
            db.commit()
            return self.send(200, {'csrf': csrf}, cookie=cookie)

        if not post and path == '/api/tasks':
            rows = db.execute('''
                SELECT
                    t.id,
                    t.title,
                    t.subject,
                    t.difficulty,
                    t.statement,
                    ROW_NUMBER() OVER (
                        PARTITION BY t.subject ORDER BY t.id
                    ) AS number,
                    EXISTS(
                        SELECT 1 FROM solves s
                        WHERE s.task_id = t.id AND s.user_id = ?
                    ) AS solved,
                    EXISTS(
                        SELECT 1 FROM reveals r
                        WHERE r.task_id = t.id AND r.user_id = ?
                    ) AS revealed,
                    EXISTS(
                        SELECT 1 FROM solves s
                        WHERE s.task_id = t.id AND s.user_id = ?
                        AND NOT EXISTS(
                            SELECT 1 FROM reveals r
                            WHERE r.user_id = s.user_id
                            AND r.task_id = s.task_id
                            AND r.before_solve = 1
                        )
                    ) AS awarded
                FROM tasks t
                ORDER BY t.id
            ''', (uid, uid, uid)).fetchall()

            return self.send(200, [dict(row) for row in rows])

        if not post and path == '/api/profile':
            user = db.execute(
                'SELECT username FROM users WHERE id = ?',
                (uid,)
            ).fetchone()

            solved = db.execute('''
                SELECT t.id, t.title, s.created_at,
                    NOT EXISTS(
                        SELECT 1 FROM reveals r
                        WHERE r.user_id = s.user_id
                        AND r.task_id = s.task_id
                        AND r.before_solve = 1
                    ) AS awarded
                FROM solves s
                JOIN tasks t ON t.id = s.task_id
                WHERE s.user_id = ?
                ORDER BY s.created_at DESC
            ''', (uid,)).fetchall()

            revealed_count = db.execute(
                'SELECT COUNT(*) FROM reveals WHERE user_id = ?',
                (uid,)
            ).fetchone()[0]

            count = db.execute(
                'SELECT COUNT(*) FROM attempts WHERE user_id = ?',
                (uid,)
            ).fetchone()[0]

            return self.send(200, {
                'username': user['username'],
                'points': sum(row['awarded'] for row in solved),
                'revealed_count': revealed_count,
                'solved_count': len(solved),
                'attempt_count': count,
                'solved': [dict(row) for row in solved]
            })

        if not post and path == '/api/history':
            rows = db.execute('''
                SELECT
                    a.id,
                    a.task_id,
                    t.title,
                    a.answer,
                    a.correct,
                    a.created_at,
                    r.created_at AS revealed_at,
                    CASE
                        WHEN r.user_id IS NULL THEN 0 ELSE 1
                    END AS revealed,
                    CASE
                        WHEN r.user_id IS NOT NULL
                        AND r.attempt_id < a.id
                        THEN 1 ELSE 0
                    END AS revealed_before_attempt,
                    CASE
                        WHEN a.correct = 1
                        AND a.id = (
                            SELECT MIN(first.id)
                            FROM attempts first
                            WHERE first.user_id = a.user_id
                            AND first.task_id = a.task_id
                            AND first.correct = 1
                        )
                        AND NOT EXISTS(
                            SELECT 1 FROM reveals block
                            WHERE block.user_id = a.user_id
                            AND block.task_id = a.task_id
                            AND block.before_solve = 1
                        )
                        THEN 1 ELSE 0
                    END AS awarded
                FROM attempts a
                JOIN tasks t ON t.id = a.task_id
                LEFT JOIN reveals r
                    ON r.user_id = a.user_id
                    AND r.task_id = a.task_id
                WHERE a.user_id = ?
                ORDER BY a.id DESC
            ''', (uid,)).fetchall()

            return self.send(200, [dict(row) for row in rows])

        if not post and path == '/api/leaderboard':
            rows = db.execute('''
                SELECT u.id, u.username, COUNT(s.task_id) AS points
                FROM users u
                LEFT JOIN solves s
                    ON s.user_id = u.id
                    AND NOT EXISTS(
                        SELECT 1 FROM reveals r
                        WHERE r.user_id = s.user_id
                        AND r.task_id = s.task_id
                        AND r.before_solve = 1
                    )
                GROUP BY u.id
                ORDER BY points DESC, u.username COLLATE NOCASE, u.id
            ''').fetchall()

            return self.send(200, [dict(row) for row in rows])

        if post and path == '/api/reveal':
            data = self.body(('task_id',))
            task_id = data['task_id']

            if type(task_id) is not int or not 1 <= task_id <= 2147483647:
                raise ApiError(400, 'Некорректный номер задачи.')

            # Просмотр и отправка ответа используют одну блокировку.
            db.execute('BEGIN IMMEDIATE')

            task = db.execute(
                'SELECT answer, explanation FROM tasks WHERE id = ?',
                (task_id,)
            ).fetchone()

            if not task:
                raise ApiError(404, 'Задача не найдена.')

            solved = db.execute(
                'SELECT 1 FROM solves WHERE user_id = ? AND task_id = ?',
                (uid, task_id)
            ).fetchone()

            cutoff = db.execute(
                'SELECT COALESCE(MAX(id), 0) '
                'FROM attempts WHERE user_id = ?',
                (uid,)
            ).fetchone()[0]

            db.execute('''
                INSERT OR IGNORE INTO reveals(
                    user_id, task_id, before_solve, attempt_id
                ) VALUES (?, ?, ?, ?)
            ''', (uid, task_id, int(not solved), cutoff))

            reveal = db.execute(
                'SELECT before_solve FROM reveals '
                'WHERE user_id = ? AND task_id = ?',
                (uid, task_id)
            ).fetchone()

            points = points_for(db, uid)
            db.commit()

            return self.send(200, {
                'answer': task['answer'],
                'explanation': task['explanation'],
                'points': points,
                'eligible': not bool(reveal['before_solve'])
            })

        if post and path == '/api/submit':
            data = self.body(('task_id', 'answer'))
            task_id = data['task_id']
            answer = data['answer']

            if (
                type(task_id) is not int
                or not 1 <= task_id <= 2147483647
                or not isinstance(answer, str)
                or not 1 <= len(answer.strip()) <= 200
            ):
                raise ApiError(
                    400,
                    'Укажите задачу и ответ длиной от 1 до 200 символов.'
                )

            answer = answer.strip()
            db.execute('BEGIN IMMEDIATE')

            task = db.execute(
                'SELECT answer FROM tasks WHERE id = ?',
                (task_id,)
            ).fetchone()

            if not task:
                raise ApiError(404, 'Задача не найдена.')

            correct = answer == task['answer']

            db.execute('''
                INSERT INTO attempts(user_id, task_id, answer, correct)
                VALUES (?, ?, ?, ?)
            ''', (uid, task_id, answer, int(correct)))

            awarded = 0

            if correct:
                awarded = db.execute('''
                    INSERT OR IGNORE INTO solves(user_id, task_id)
                    VALUES (?, ?)
                ''', (uid, task_id)).rowcount

            blocked = db.execute('''
                SELECT 1 FROM reveals
                WHERE user_id = ?
                AND task_id = ?
                AND before_solve = 1
            ''', (uid, task_id)).fetchone()

            eligible = not bool(blocked)
            awarded = awarded if eligible else 0
            points = points_for(db, uid)
            db.commit()

            return self.send(200, {
                'correct': correct,
                'awarded': awarded,
                'points': points,
                'eligible': eligible
            })

        raise ApiError(404, 'Адрес не найден.')


def create_server(host='127.0.0.1', port=8000, db_path=None):
    path = str(db_path or ROOT / 'data' / 'olymp.sqlite3')
    init_db(path)

    server = ThreadingHTTPServer((host, port), Handler)
    server.db_path = path
    server.secure_cookie = os.environ.get('OLYMP_SECURE_COOKIE') == '1'
    server.daemon_threads = True

    return server


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Олимп — платформа подготовки к олимпиадам'
    )
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--db', default=None)
    args = parser.parse_args()

    server = create_server(args.host, args.port, args.db)
    print(
        f'Олимп запущен: http://{args.host}:{server.server_port}',
        flush=True
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nСервер остановлен.')
    finally:
        server.server_close()