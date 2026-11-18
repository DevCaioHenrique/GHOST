import sqlite3, json, os, re
from datetime import datetime
from contextlib import contextmanager
P = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'ghost.db')
MIGRATIONS = ['''
create table collections(id integer primary key,name text unique not null,descr text default '',icon text default '◈',color text default '#00BFFF',archived int default 0,pos int default 0);
create table fields(id integer primary key,cid int not null references collections(id) on delete cascade,name text not null,type text not null,required int default 0,visible int default 1,pos int default 0,dflt text,opts text default '{}');
create table records(id integer primary key,cid int not null references collections(id) on delete cascade,data text not null default '{}',created text,updated text);
create table refs(rid int not null references records(id) on delete cascade,fid int not null references fields(id) on delete cascade,tid int not null references records(id) on delete cascade,primary key(rid,fid,tid));
create table chat(id integer primary key,role text,content text,meta text,ts text);
create table settings(k text primary key,v text);''']
def upgrade1(c):
    c.execute("alter table collections add column kind text default 'generic'")
    ids = {}
    for n, k in {'Pessoas': 'pessoa', 'Diário': 'diario', 'Interesses': 'interesse', 'Grupos': 'grupo'}.items():
        r = c.execute('select id from collections where name=?', (n,)).fetchone()
        if r: ids[n] = r[0]; c.execute('update collections set kind=? where id=?', (k, r[0]))
    def add(cn, name, t, **o):
        if cn not in ids or c.execute('select 1 from fields where cid=? and name=?', (ids[cn], name)).fetchone(): return
        if 'target' in o:
            if o['target'] not in ids: return
            o['target'] = ids[o['target']]
        c.execute('insert into fields(cid,name,type,pos,opts) values(?,?,?,(select count(*) from fields where cid=?),?)', (ids[cn], name, t, ids[cn], json.dumps(o)))
    add('Pessoas', 'Características', 'tags'); add('Pessoas', 'Pessoas relacionadas', 'refs', target='Pessoas')
    add('Diário', 'Grupos', 'refs', target='Grupos'); add('Grupos', 'Integrantes', 'refs', target='Pessoas'); add('Interesses', 'Relacionados', 'refs', target='Interesses')
MIGRATIONS.append(upgrade1)
TYPES = ['text','longtext','number','date','datetime','bool','select','tags','url','email','phone','ref','refs']
DEFAULTS = {'url': 'http://localhost:11434', 'model': 'qwen2.5:3b'}
now = lambda: datetime.now().isoformat(timespec='seconds')

@contextmanager
def tx():
    c = sqlite3.connect(P, timeout=10); c.row_factory = sqlite3.Row
    c.execute('pragma foreign_keys=on')
    try: yield c; c.commit()
    except Exception: c.rollback(); raise
    finally: c.close()

def init():
    os.makedirs(os.path.dirname(P), exist_ok=True)
    with tx() as c:
        v = c.execute('pragma user_version').fetchone()[0]
        for i in range(v, len(MIGRATIONS)):
            m = MIGRATIONS[i]
            if isinstance(m, str): c.executescript(m)
            else: m(c)
            if i == 0 and not c.execute('select 1 from collections').fetchone(): seed(c)
            c.execute(f'pragma user_version={i+1}')

def seed(c):
    ids = {}
    for i, (n, d, ic, co) in enumerate([('Pessoas','Pessoas que você conhece','☺','#00BFFF'),('Diário','Acontecimentos e experiências','▤','#FF9F1C'),('Interesses','Assuntos e conhecimentos','✦','#B15CFF'),('Grupos','Círculos e relações','⬡','#7ED957')]):
        ids[n] = c.execute('insert into collections(name,descr,icon,color,pos) values(?,?,?,?,?)', (n,d,ic,co,i)).lastrowid
    def F(cn, name, t, req=0, **o): c.execute('insert into fields(cid,name,type,required,pos,opts) values(?,?,?,?,(select count(*) from fields where cid=?),?)', (ids[cn],name,t,req,ids[cn],json.dumps(o)))
    F('Pessoas','Nome','text',1); F('Pessoas','Apelido','text'); F('Pessoas','Aniversário','date'); F('Pessoas','E-mail','email'); F('Pessoas','Notas','longtext')
    F('Pessoas','Grupos','refs',target=ids['Grupos']); F('Pessoas','Interesses','refs',target=ids['Interesses'])
    F('Diário','Título','text',1); F('Diário','Data','date'); F('Diário','Texto','longtext'); F('Diário','Pessoas','refs',target=ids['Pessoas']); F('Diário','Interesses','refs',target=ids['Interesses'])
    F('Interesses','Nome','text',1); F('Interesses','Categoria','text'); F('Interesses','Descrição','longtext')
    F('Grupos','Nome','text',1); F('Grupos','Descrição','longtext')

def setting(k):
    with tx() as c:
        r = c.execute('select v from settings where k=?', (k,)).fetchone()
    return r[0] if r else DEFAULTS.get(k)
def set_setting(k, v):
    with tx() as c: c.execute('insert or replace into settings values(?,?)', (k, v))

def fields_of(c, cid):
    return [dict(r) for r in c.execute('select * from fields where cid=? order by pos,id', (cid,))]
def title(c, rid):
    r = c.execute('select cid,data from records where id=?', (rid,)).fetchone()
    f = c.execute('select id from fields where cid=? order by pos,id limit 1', (r['cid'],)).fetchone() if r else None
    return json.loads(r['data']).get(str(f['id'])) if f else None
def rec(c, rid):
    r = c.execute('select * from records where id=?', (rid,)).fetchone()
    if not r: raise LookupError('Registro não encontrado')
    d = json.loads(r['data'])
    for f in c.execute("select id,type from fields where cid=? and type in('ref','refs')", (r['cid'],)):
        ids = [x[0] for x in c.execute('select tid from refs where rid=? and fid=?', (rid, f['id']))]
        d[str(f['id'])] = (ids[0] if ids else None) if f['type'] == 'ref' else ids
    return {'id': rid, 'cid': r['cid'], 'data': d, 'title': title(c, rid), 'created': r['created'], 'updated': r['updated']}

def validate(c, f, v):
    t = f['type']; o = json.loads(f['opts'] or '{}')
    if v is None or v == '' or v == []:
        if f['required']: raise ValueError(f"O campo '{f['name']}' é obrigatório")
        return None
    try:
        if t in ('text','longtext','phone'): return str(v).strip()
        if t == 'email':
            if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', str(v)): raise ValueError
            return str(v)
        if t == 'url':
            if not re.match(r'https?://', str(v)): raise ValueError
            return str(v)
        if t == 'number': return float(v)
        if t == 'date': datetime.strptime(str(v), '%Y-%m-%d'); return str(v)
        if t == 'datetime': datetime.fromisoformat(str(v)); return str(v)
        if t == 'bool': return v in (True, 'true', 1, '1', 'sim')
        if t == 'select':
            if v not in o.get('choices', []): raise ValueError
            return v
        if t == 'tags': return [str(x).strip() for x in (v if isinstance(v, list) else str(v).split(',')) if str(x).strip()]
        if t in ('ref', 'refs'):
            ids = [int(x) for x in (v if isinstance(v, list) else [v])]
            for i in ids:
                if not c.execute('select 1 from records where id=? and cid=?', (i, o.get('target'))).fetchone(): raise ValueError
            return ids[:1] if t == 'ref' else list(dict.fromkeys(ids))
    except (ValueError, TypeError):
        raise ValueError(f"Valor inválido para '{f['name']}' (tipo {t})")

def save(c, cid, data, rid=None):
    if not c.execute('select 1 from collections where id=?', (cid,)).fetchone(): raise LookupError('Coleção não encontrada')
    fs = fields_of(c, cid); old = rec(c, rid)['data'] if rid else {}; new = {}
    for f in fs:
        k = str(f['id'])
        new[k] = validate(c, f, data[k] if k in data else (old.get(k) if rid else f['dflt']))
    if not rid: rid = c.execute('insert into records(cid,data,created,updated) values(?,?,?,?)', (cid, '{}', now(), now())).lastrowid
    c.execute('delete from refs where rid=?', (rid,)); plain = {}
    for f in fs:
        v = new[str(f['id'])]
        if f['type'] in ('ref', 'refs'):
            for t in v or []: c.execute('insert or ignore into refs values(?,?,?)', (rid, f['id'], t))
        else: plain[str(f['id'])] = v
    c.execute('update records set data=?,updated=? where id=?', (json.dumps(plain, ensure_ascii=False), now(), rid))
    return rid

def brief(c, rid):
    r = c.execute('select r.id,r.cid,c.name,c.icon,c.color from records r join collections c on c.id=r.cid where r.id=?', (rid,)).fetchone()
    return {'id': rid, 'titulo': title(c, rid), 'colecao': r['name'], 'cid': r['cid'], 'icon': r['icon'], 'color': r['color']} if r else None

def search(c, q, cid=None, limit=40):
    toks = q.lower().split(); out = []
    for r in c.execute('select id,cid,data from records' + (' where cid=?' if cid else ' order by updated desc'), (cid,) if cid else ()):
        t = (title(c, r['id']) or '')
        if all(k in (r['data'] + ' ' + str(t)).lower() for k in toks):
            b = brief(c, r['id']); b['resumo'] = ' · '.join(str(v)[:60] for v in json.loads(r['data']).values() if v and v != t)[:140]; out.append(b)
            if len(out) >= limit: break
    return out
