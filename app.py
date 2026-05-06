"""
CareBlock Clinical Operations — Backend Server
Deploy to Railway: railway.app (free to start)
Run locally: python app.py
"""

import sqlite3, json, os
from datetime import datetime
from flask import Flask, request, jsonify, send_file, g

app = Flask(__name__)
DB  = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'careblock.db')

def get_db():
    db = getattr(g, '_db', None)
    if db is None:
        db = g._db = sqlite3.connect(DB)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, '_db', None)
    if db: db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.executescript('''
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
        db.commit()

@app.after_request
def cors(r):
    r.headers['Access-Control-Allow-Origin'] = '*'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    r.headers['Access-Control-Allow-Methods'] = 'GET,POST,PUT,DELETE,OPTIONS'
    return r

@app.route('/', defaults={'p': ''}, methods=['OPTIONS'])
@app.route('/<path:p>', methods=['OPTIONS'])
def opts(p): return '', 200

@app.route('/')
def index():
    portal = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'clinical_portal.html')
    if os.path.exists(portal):
        return send_file(portal)
    return '<h2>clinical_portal.html not found</h2>', 404

@app.route('/clinical_portal.html')
def portal_direct(): return index()

@app.route('/api/requests', methods=['GET'])
def get_requests():
    rows = get_db().execute('SELECT data FROM requests ORDER BY id').fetchall()
    out = []
    for row in rows:
        try: out.append(json.loads(row['data']))
        except: pass
    return jsonify(out)

@app.route('/api/requests', methods=['POST'])
def save_requests():
    data = request.get_json() or []
    db   = get_db()
    db.execute('DELETE FROM requests')
    now  = datetime.utcnow().isoformat()
    for item in data:
        db.execute('INSERT INTO requests (data,created_at,updated_at) VALUES (?,?,?)',
                   (json.dumps(item), now, now))
    db.commit()
    return jsonify({'ok': True, 'count': len(data)})

@app.route('/api/requests/<int:rid>', methods=['PUT'])
def update_one(rid):
    data = request.get_json()
    if not data: return jsonify({'ok': False}), 400
    db  = get_db()
    now = datetime.utcnow().isoformat()
    for row in db.execute('SELECT rowid, data FROM requests').fetchall():
        try:
            item = json.loads(row['data'])
            if int(item.get('id',-1)) == rid:
                db.execute('UPDATE requests SET data=?,updated_at=? WHERE rowid=?',
                           (json.dumps(data), now, row['rowid']))
                db.commit()
                return jsonify({'ok': True})
        except: continue
    db.execute('INSERT INTO requests (data,created_at,updated_at) VALUES (?,?,?)',
               (json.dumps(data), now, now))
    db.commit()
    return jsonify({'ok': True, 'inserted': True})

@app.route('/api/settings/<key>', methods=['GET'])
def get_setting(key):
    row = get_db().execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return jsonify({'key': key, 'value': json.loads(row['value']) if row else None})

@app.route('/api/settings/<key>', methods=['POST'])
def save_setting(key):
    db = get_db()
    db.execute(
        'INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) '
        'ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',
        (key, json.dumps(request.get_json()), datetime.utcnow().isoformat()))
    db.commit()
    return jsonify({'ok': True})

@app.route('/api/backups', methods=['GET'])
def get_backups():
    rows = get_db().execute('SELECT id,label,created_at FROM backups ORDER BY id DESC LIMIT 5').fetchall()
    return jsonify([{'id':r['id'],'label':r['label'],'timestamp':r['created_at']} for r in rows])

@app.route('/api/backups', methods=['POST'])
def create_backup():
    body = request.get_json() or {}
    db   = get_db()
    count = db.execute('SELECT COUNT(*) FROM backups').fetchone()[0]
    if count >= 5:
        oldest = db.execute('SELECT id FROM backups ORDER BY id LIMIT 1').fetchone()
        if oldest: db.execute('DELETE FROM backups WHERE id=?', (oldest['id'],))
    db.execute('INSERT INTO backups(label,data,created_at) VALUES(?,?,?)',
               (body.get('label','Backup'), json.dumps(body.get('data',{})),
                datetime.utcnow().isoformat()))
    db.commit()
    return jsonify({'ok': True})

@app.route('/api/backups/<int:bk_id>', methods=['GET'])
def get_backup(bk_id):
    row = get_db().execute('SELECT data FROM backups WHERE id=?', (bk_id,)).fetchone()
    if row: return jsonify(json.loads(row['data']))
    return jsonify({'error': 'not found'}), 404

@app.route('/api/status')
def status():
    cnt = get_db().execute('SELECT COUNT(*) FROM requests').fetchone()[0]
    return jsonify({'ok': True, 'requests': cnt, 'brand': 'CareBlock Clinical Operations', 'version': '2.0'})

if __name__ == '__main__':
    init_db()
    import socket
    try: ip = socket.gethostbyname(socket.gethostname())
    except: ip = '127.0.0.1'
    port = int(os.environ.get('PORT', 5000))
    print(f'\n CareBlock Server v2.0')
    print(f'  Local:        http://localhost:{port}')
    print(f'  Network:      http://{ip}:{port}')
    print(f'  Doctor link:  http://{ip}:{port}/?role=doctor')
    print(f'  Team link:    http://{ip}:{port}/?role=team\n')
    # Use gunicorn in production (Railway), flask dev server locally
    app.run(host='0.0.0.0', port=port, debug=False)
