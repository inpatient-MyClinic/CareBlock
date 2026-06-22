"""
MyClinic Doctor Onboarding — Flask drop-in module.

Adds these endpoints to your existing Flask `app`:
    GET    /onboarding                       → serves the HTML
    GET    /api/onboarding/health            → heartbeat
    GET    /api/onboarding/doctors           → list all doctors (newest first)
    GET    /api/onboarding/doctors/<id>      → single doctor
    POST   /api/onboarding/doctors           → upsert doctor (body = doctor JSON)
    DELETE /api/onboarding/doctors/<id>      → remove doctor

Persistence:
    Reads DATABASE_URL from the environment (Railway auto-injects this when
    a Postgres service is attached). Auto-creates the `doctors` table on
    startup (id text PK, created_at bigint, data jsonb).

Usage (one line in your app.py):
    from doctor_onboarding import register_doctor_onboarding
    register_doctor_onboarding(app)

That's it.
"""

import os
import json
import time
from flask import request, jsonify, send_from_directory, abort

try:
    import psycopg2
    from psycopg2.extras import Json
    from psycopg2.pool import ThreadedConnectionPool
except ImportError:
    psycopg2 = None
    Json = None
    ThreadedConnectionPool = None


_pool = None


def _build_pool():
    """Lazily create a connection pool from DATABASE_URL."""
    global _pool
    if _pool is not None:
        return _pool
    if psycopg2 is None:
        print('[doctor_onboarding] psycopg2 not installed — add psycopg2-binary to requirements.txt')
        return None
    url = os.environ.get('DATABASE_URL')
    if not url:
        print('[doctor_onboarding] DATABASE_URL not set — attach a Postgres service in Railway')
        return None
    # Railway / most managed Postgres providers require SSL
    sslmode = 'require' if any(h in url for h in ('railway', 'amazonaws', 'render', 'supabase', 'neon')) else 'prefer'
    try:
        _pool = ThreadedConnectionPool(1, 10, url, sslmode=sslmode)
        return _pool
    except Exception as exc:
        print('[doctor_onboarding] Could not create pool:', exc)
        return None


def _conn():
    pool = _build_pool()
    if pool is None:
        raise RuntimeError('Database not configured (DATABASE_URL missing or psycopg2 not installed)')
    return pool.getconn()


def _release(conn):
    if _pool is not None and conn is not None:
        _pool.putconn(conn)


def _ensure_schema():
    """Create the doctors table + index on first call. Idempotent."""
    conn = None
    try:
        conn = _conn()
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS doctors (
                  id          text PRIMARY KEY,
                  created_at  bigint,
                  data        jsonb
                );
                CREATE INDEX IF NOT EXISTS doctors_created_at_idx
                    ON doctors (created_at DESC);
            """)
            conn.commit()
        print('[doctor_onboarding] ✓ doctors table ready')
    except Exception as exc:
        print('[doctor_onboarding] schema init failed:', exc)
    finally:
        _release(conn)


def register_doctor_onboarding(app, html_filename='doctor_onboarding.html'):
    """Wire the routes onto the given Flask `app`."""

    base_dir = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(base_dir, html_filename)

    # Initialize the schema once at import time (safe to call multiple times).
    _ensure_schema()

    # --- CORS only for our API namespace (won't touch your existing routes) ---
    @app.after_request
    def _onb_cors(resp):
        if request.path.startswith('/api/onboarding'):
            resp.headers['Access-Control-Allow-Origin'] = '*'
            resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, DELETE, OPTIONS'
            resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp

    # Preflight
    @app.route('/api/onboarding/doctors', methods=['OPTIONS'])
    @app.route('/api/onboarding/doctors/<doc_id>', methods=['OPTIONS'])
    def _onb_preflight(doc_id=None):
        return ('', 204)

    # --- The HTML page ---
    @app.route('/onboarding')
    def doctor_onboarding_page():
        if not os.path.exists(html_path):
            return ('doctor_onboarding.html not found in the project root', 404)
        return send_from_directory(base_dir, html_filename)

    # --- API: health ---
    @app.route('/api/onboarding/health')
    def doctor_onboarding_health():
        db_ok = False
        try:
            conn = _conn()
            with conn.cursor() as cur:
                cur.execute('SELECT 1')
            db_ok = True
            _release(conn)
        except Exception:
            pass
        return jsonify(ok=True, db=db_ok, ts=int(time.time() * 1000))

    # --- API: list ---
    @app.route('/api/onboarding/doctors', methods=['GET'])
    def doctor_onboarding_list():
        conn = None
        try:
            conn = _conn()
            with conn.cursor() as cur:
                cur.execute('SELECT data FROM doctors ORDER BY created_at DESC')
                rows = [r[0] for r in cur.fetchall()]
            return jsonify(rows)
        except Exception as exc:
            return jsonify(error=str(exc)), 500
        finally:
            _release(conn)

    # --- API: single ---
    @app.route('/api/onboarding/doctors/<doc_id>', methods=['GET'])
    def doctor_onboarding_get(doc_id):
        conn = None
        try:
            conn = _conn()
            with conn.cursor() as cur:
                cur.execute('SELECT data FROM doctors WHERE id=%s', (doc_id,))
                row = cur.fetchone()
            if not row:
                return jsonify(error='not found'), 404
            return jsonify(row[0])
        except Exception as exc:
            return jsonify(error=str(exc)), 500
        finally:
            _release(conn)

    # --- API: upsert ---
    @app.route('/api/onboarding/doctors', methods=['POST'])
    def doctor_onboarding_upsert():
        doc = request.get_json(silent=True) or {}
        if not doc.get('id'):
            return jsonify(error='id required'), 400
        conn = None
        try:
            conn = _conn()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO doctors (id, created_at, data)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (id) DO UPDATE
                       SET created_at = EXCLUDED.created_at,
                           data       = EXCLUDED.data
                    """,
                    (doc['id'], doc.get('createdAt', int(time.time() * 1000)), Json(doc)),
                )
                conn.commit()
            return jsonify(ok=True)
        except Exception as exc:
            return jsonify(error=str(exc)), 500
        finally:
            _release(conn)

    # --- API: delete ---
    @app.route('/api/onboarding/doctors/<doc_id>', methods=['DELETE'])
    def doctor_onboarding_delete(doc_id):
        conn = None
        try:
            conn = _conn()
            with conn.cursor() as cur:
                cur.execute('DELETE FROM doctors WHERE id=%s', (doc_id,))
                conn.commit()
            return jsonify(ok=True)
        except Exception as exc:
            return jsonify(error=str(exc)), 500
        finally:
            _release(conn)

    print('[doctor_onboarding] registered routes:')
    print('   GET  /onboarding')
    print('   GET  /api/onboarding/health')
    print('   GET  /api/onboarding/doctors')
    print('   GET  /api/onboarding/doctors/<id>')
    print('   POST /api/onboarding/doctors')
    print('   DEL  /api/onboarding/doctors/<id>')

    return app
