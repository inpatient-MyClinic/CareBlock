"""
CareBlock Clinical Operations — Backend Server v3.1
Serves any HTML file that starts with 'clinical_portal'
"""

import sqlite3, json, os, glob
from datetime import datetime
from flask import Flask, request, jsonify, send_file, g

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, 'careblock.db')

# ─── Find the portal HTML (any name starting with clinical_portal) ───
def find_portal():
    # Try exact name first
    exact = os.path.join(BASE_DIR, 'clinical_portal.html')
    if os.path.exists(exact):
        return exact
    # Try any file matching clinical_portal*.html
    matches = glob.glob(os.path.join(BASE_DIR, 'clinical_portal*.html'))
    if matches:
        return matches[0]
    # Try any .html file
    matches = glob.glob(os.path.join(BASE_DIR, '*.html'))
    if matches:
        return matches[0]
    return None

# ─── Database ────────────────────────────────────────────────────────
def get_db():
    db = getattr(g, '_db', None)
    if db is None:
        db = g._db = sqlite3.connect(DB)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
    return db

@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, '_db', None)
    if db: db.close()

def init_db():
    conn = sqlite3.connect(DB)
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS backups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT,
            data TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
    ''')
    conn.commit()
    conn.close()

# Run init immediately
try:
    init_db()
    print(f"[CareBlock] DB ready: {DB}")
    portal = find_portal()
    print(f"[CareBlock] Portal: {portal or 'NOT FOUND'}")
except Exception as e:
    print(f"[CareBlock] Init error: {e}")

# ─── CORS ────────────────────────────────────────────────────────────
@app.after_request
def cors(r):
    r.headers['Access-Control-Allow-Origin'] = '*'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    r.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    return r

@app.route('/', defaults={'p': ''}, methods=['OPTIONS'])
@app.route('/<path:p>', methods=['OPTIONS'])
def opts(p): return '', 200

# ─── Serve portal ────────────────────────────────────────────────────
@app.route('/')
def index():
    portal = find_portal()
    if portal:
        return send_file(portal)
    # List what files exist
    files = os.listdir(BASE_DIR)
    return f'Portal not found. Files in directory: {files}', 404

@app.route('/clinical_portal.html')
def portal_direct(): return index()

# ─── Requests ────────────────────────────────────────────────────────
@app.route('/api/requests', methods=['GET'])
def get_requests():
    try:
        rows = get_db().execute('SELECT data FROM requests ORDER BY id').fetchall()
        return jsonify([json.loads(r['data']) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/requests', methods=['POST'])
def save_requests():
    try:
        data = request.get_json(force=True) or []
        db = get_db()
        db.execute('DELETE FROM requests')
        now = datetime.utcnow().isoformat()
        for item in data:
            db.execute('INSERT INTO requests (data,created_at,updated_at) VALUES(?,?,?)',
                       (json.dumps(item), now, now))
        db.commit()
        return jsonify({'ok': True, 'count': len(data)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/requests/<int:rid>', methods=['PUT'])
def update_one(rid):
    try:
        data = request.get_json(force=True)
        db = get_db()
        now = datetime.utcnow().isoformat()
        for row in db.execute('SELECT rowid, data FROM requests').fetchall():
            item = json.loads(row['data'])
            if int(item.get('id', -1)) == rid:
                db.execute('UPDATE requests SET data=?,updated_at=? WHERE rowid=?',
                           (json.dumps(data), now, row['rowid']))
                db.commit()
                return jsonify({'ok': True})
        db.execute('INSERT INTO requests (data,created_at,updated_at) VALUES(?,?,?)',
                   (json.dumps(data), now, now))
        db.commit()
        return jsonify({'ok': True, 'inserted': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

# ─── Settings ────────────────────────────────────────────────────────
@app.route('/api/settings/<key>', methods=['GET'])
def get_setting(key):
    try:
        row = get_db().execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return jsonify({'key': key, 'value': json.loads(row['value']) if row else None})
    except Exception as e:
        return jsonify({'key': key, 'value': None, 'error': str(e)})

@app.route('/api/settings/<key>', methods=['POST'])
def save_setting(key):
    try:
        val = request.get_json(force=True)
        db = get_db()
        db.execute(
            'INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) '
            'ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',
            (key, json.dumps(val), datetime.utcnow().isoformat()))
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

# ─── Backups ─────────────────────────────────────────────────────────
@app.route('/api/backups', methods=['GET'])
def get_backups():
    try:
        rows = get_db().execute(
            'SELECT id,label,created_at FROM backups ORDER BY id DESC LIMIT 5').fetchall()
        return jsonify([{'id': r['id'], 'label': r['label'],
                         'timestamp': r['created_at']} for r in rows])
    except Exception as e:
        return jsonify([])

@app.route('/api/backups', methods=['POST'])
def create_backup():
    try:
        body = request.get_json(force=True) or {}
        db = get_db()
        count = db.execute('SELECT COUNT(*) FROM backups').fetchone()[0]
        if count >= 5:
            oldest = db.execute('SELECT id FROM backups ORDER BY id LIMIT 1').fetchone()
            if oldest: db.execute('DELETE FROM backups WHERE id=?', (oldest['id'],))
        db.execute('INSERT INTO backups(label,data,created_at) VALUES(?,?,?)',
                   (body.get('label', 'Backup'),
                    json.dumps(body.get('data', {})),
                    datetime.utcnow().isoformat()))
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/backups/<int:bk_id>')
def get_backup(bk_id):
    try:
        row = get_db().execute('SELECT data FROM backups WHERE id=?', (bk_id,)).fetchone()
        if row: return jsonify(json.loads(row['data']))
        return jsonify({'error': 'not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ─── Status ──────────────────────────────────────────────────────────
@app.route('/api/status')
def status():
    try:
        cnt = get_db().execute('SELECT COUNT(*) FROM requests').fetchone()[0]
        portal = find_portal()
        return jsonify({'ok': True, 'requests': cnt,
                        'brand': 'CareBlock Clinical Operations',
                        'version': '3.1',
                        'portal_file': os.path.basename(portal) if portal else None})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

# ─── Start ───────────────────────────────────────────────────────────



if __name__ == '__main__':
    import socket
    try: ip = socket.gethostbyname(socket.gethostname())
    except: ip = '127.0.0.1'
    port = int(os.environ.get('PORT', 5000))
    print(f'\n CareBlock v3.1 — http://{ip}:{port}\n')
    app.run(host='0.0.0.0', port=port, debug=False)
