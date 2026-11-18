import json
from functools import wraps
from flask import Flask, jsonify, request
import db, ai
app = Flask(__name__, static_folder='static', static_url_path='/static'); db.init()

def api(f):
    @wraps(f)
    def w(*a, **k):
        try: return jsonify(f(*a, **k))
        except ValueError as e: return jsonify(error=str(e)), 400
        except LookupError as e: return jsonify(error=str(e)), 404
        except RuntimeError as e: return jsonify(error=str(e)), 503
        except Exception as e: return jsonify(error=f'Erro interno: {e}'), 500
    return w

@app.get('/')
def index(): return app.send_static_file('index.html')

def colls(c):
    out = []
    for r in c.execute('select c.*,(select count(*) from records where cid=c.id) n from collections c order by pos,id'):
        d = dict(r); d['fields'] = [dict(f, opts=json.loads(f['opts'])) for f in db.fields_of(c, r['id'])]; out.append(d)
    return out

@app.get('/api/collections')
@api
def l_cols():
    with db.tx() as c: return colls(c)

@app.post('/api/collections')
@api
def n_col():
    b = request.json or {}
    if not str(b.get('name', '')).strip(): raise ValueError('Informe o nome da coleção')
    with db.tx() as c:
        try: cid = c.execute('insert into collections(name,descr,icon,color,pos) values(?,?,?,?,(select count(*) from collections))', (b['name'].strip(), b.get('descr', ''), b.get('icon') or '◈', b.get('color') or '#00BFFF')).lastrowid
        except Exception: raise ValueError('Já existe uma coleção com esse nome')
        c.execute("insert into fields(cid,name,type,required,pos) values(?,'Nome','text',1,0)", (cid,)); c.execute('update collections set kind=? where id=?', (b.get('kind') or 'generic', cid)); return {'id': cid}

@app.put('/api/collections/<int:cid>')
@api
def e_col(cid):
    b = request.json or {}
    with db.tx() as c:
        for k in ('name', 'descr', 'icon', 'color', 'archived', 'kind'):
            if k in b: c.execute(f'update collections set {k}=? where id=?', (b[k], cid))
    return {'ok': True}

@app.delete('/api/collections/<int:cid>')
@api
def d_col(cid):
    with db.tx() as c:
        for f in c.execute("select id,opts from fields where type in('ref','refs')").fetchall():
            if json.loads(f['opts']).get('target') == cid: c.execute('delete from fields where id=?', (f['id'],))
        c.execute('delete from collections where id=?', (cid,))
    return {'ok': True}

@app.put('/api/collections/<int:cid>/fields')
@api
def e_fields(cid):
    fs = request.json or []
    if not fs or not fs[0].get('required') or fs[0].get('type') in ('ref', 'refs', 'bool'): raise ValueError('O primeiro campo é o principal: obrigatório e de texto, número ou data')
    with db.tx() as c:
        keep = [f['id'] for f in fs if f.get('id')]
        for r in c.execute('select id from fields where cid=?', (cid,)).fetchall():
            if r[0] not in keep: c.execute('delete from fields where id=?', (r[0],))
        for i, f in enumerate(fs):
            if not str(f.get('name', '')).strip() or f.get('type') not in db.TYPES: raise ValueError('Campo sem nome ou com tipo inválido')
            o = f.get('opts') or {}
            if f['type'] in ('ref', 'refs') and not c.execute('select 1 from collections where id=?', (o.get('target'),)).fetchone(): raise ValueError(f"Escolha a coleção de destino de '{f['name']}'")
            v = (f['name'].strip(), f['type'], int(bool(f.get('required'))), int(f.get('visible', 1)), i, f.get('dflt') or None, json.dumps(o))
            if f.get('id'): c.execute('update fields set name=?,type=?,required=?,visible=?,pos=?,dflt=?,opts=? where id=? and cid=?', v + (f['id'], cid))
            else: c.execute('insert into fields(name,type,required,visible,pos,dflt,opts,cid) values(?,?,?,?,?,?,?,?)', v + (cid,))
    return {'ok': True}

@app.get('/api/collections/<int:cid>/records')
@api
def l_recs(cid):
    q = request.args.get('q', '').lower()
    with db.tx() as c:
        out = [db.rec(c, r[0]) for r in c.execute('select id from records where cid=? order by id desc', (cid,))]
        if q: out = [r for r in out if q in json.dumps(r['data'], ensure_ascii=False).lower() + str(r['title']).lower()]
        lab = {i: db.title(c, i) for r in out for v in r['data'].values() for i in (v if isinstance(v, list) else [v]) if isinstance(v, (int, list)) and not isinstance(v, bool) and i is not None and isinstance(i, int)}
        return {'records': out, 'labels': lab}

@app.get('/api/records/<int:rid>')
@api
def g_rec(rid):
    with db.tx() as c: return db.rec(c, rid)

@app.get('/api/records/<int:rid>/detail')
@api
def detalhe(rid):
    with db.tx() as c:
        r = db.rec(c, rid); L = {}
        for f in db.fields_of(c, r['cid']):
            if f['type'] in ('ref', 'refs'):
                v = r['data'].get(str(f['id'])); 
                for i in ([v] if f['type'] == 'ref' else v or []):
                    if i: L[i] = db.title(c, i)
        rel = []
        for x in c.execute('select rid,fid from refs where tid=?', (rid,)).fetchall():
            b = db.brief(c, x[0]); d = db.rec(c, x[0]); b['fid'] = x[1]
            b['data'] = next((d['data'][str(f['id'])] for f in db.fields_of(c, d['cid']) if f['type'] in ('date', 'datetime') and d['data'].get(str(f['id']))), '')
            b['resumo'] = next((str(v)[:140] for f in db.fields_of(c, d['cid']) if f['type'] == 'longtext' and (v := d['data'].get(str(f['id'])))), '')
            rel.append(b)
        return {'rec': r, 'labels': L, 'rel': rel}

@app.post('/api/collections/<int:cid>/records')
@api
def n_rec(cid):
    with db.tx() as c: return {'id': db.save(c, cid, (request.json or {}).get('data', {}))}

@app.put('/api/records/<int:rid>')
@api
def e_rec(rid):
    with db.tx() as c: return {'id': db.save(c, db.rec(c, rid)['cid'], (request.json or {}).get('data', {}), rid)}

@app.post('/api/records/delete')
@api
def d_recs():
    with db.tx() as c:
        for i in (request.json or {}).get('ids', []): c.execute('delete from records where id=?', (int(i),))
    return {'ok': True}

@app.get('/api/search')
@api
def srch():
    with db.tx() as c: return db.search(c, request.args.get('q', ''))

@app.get('/api/stats')
@api
def stats():
    with db.tx() as c:
        cols = [x for x in colls(c) if not x['archived']]
        rec = [dict(db.brief(c, r[0]), updated=r[1]) for r in c.execute('select id,updated from records order by updated desc limit 6')]
        return {'cols': cols, 'total': sum(x['n'] for x in cols), 'recent': rec, 'db': 'data/ghost.db'}

@app.route('/api/settings', methods=['GET', 'PUT'])
@api
def sett():
    if request.method == 'PUT':
        for k in ('url', 'model'):
            if k in (request.json or {}): db.set_setting(k, request.json[k].strip())
    return {k: db.setting(k) for k in db.DEFAULTS}

@app.get('/api/ai/status')
@api
def ai_st(): return ai.status()
@app.post('/api/ai/load')
@api
def ai_ld(): return ai.load()
@app.post('/api/ai/chat')
@api
def ai_ch():
    t = str((request.json or {}).get('message', '')).strip()
    if not t: raise ValueError('Mensagem vazia')
    return ai.chat(t)
@app.get('/api/ai/history')
@api
def ai_h():
    with db.tx() as c: return [{'role': r['role'], 'content': r['content'], 'meta': json.loads(r['meta'] or '{}')} for r in c.execute('select * from chat order by id desc limit 40')][::-1]
@app.post('/api/ai/clear')
@api
def ai_c():
    with db.tx() as c: c.execute('delete from chat')
    return {'ok': True}

if __name__ == '__main__': app.run('127.0.0.1', 5000, debug=False)
