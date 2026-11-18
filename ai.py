import json, urllib.request, urllib.error, db
SYS = ("Você é o GHOST Assistant, assistente pessoal local do usuário. Responda em português, de forma natural e objetiva. "
"Para qualquer pergunta sobre dados do usuário, use SEMPRE as ferramentas; nunca invente registros. Se a ferramenta não retornar nada, diga que não encontrou. "
"Separe o que veio do banco do que é conhecimento geral seu (diga 'Informação geral:'). Para excluir, use excluir_registro (o usuário confirma na interface). "
"Nunca peça ao usuário algo que o sistema descobre sozinho (coleção, campos, ids): use as ferramentas. Contagens: contar_registros. Perguntas sobre uma pessoa/assunto: buscar e ver_registro. Relacionamentos: relacionados. Se faltar um campo obrigatório para criar, aí sim pergunte. Nunca peça nem execute SQL.")
import re, unicodedata

def call(path, body=None, timeout=180):
    req = urllib.request.Request(db.setting('url').rstrip('/') + path, data=json.dumps(body).encode() if body is not None else None, headers={'Content-Type': 'application/json'})
    try: return json.load(urllib.request.urlopen(req, timeout=timeout))
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
        raise RuntimeError(f'Ollama indisponível em {db.setting("url")}. Inicie o serviço (ollama serve). Detalhe: {e}')

def status():
    try: models = [m['name'] for m in call('/api/tags', timeout=4).get('models', [])]
    except RuntimeError as e: return {'online': False, 'error': str(e), 'models': [], 'model': db.setting('model'), 'available': False}
    m = db.setting('model'); return {'online': True, 'models': models, 'model': m, 'available': m in models or m + ':latest' in models}
def load(): call('/api/generate', {'model': db.setting('model'), 'keep_alive': '15m'}); return {'ok': True}

def col(c, name):
    for r in c.execute('select * from collections where archived=0'):
        if r['name'].lower() == str(name).strip().lower(): return r
    raise ValueError(f"Coleção '{name}' não existe. Existentes: " + ', '.join(r[0] for r in c.execute('select name from collections where archived=0')))

def to_ids(c, cid, vals, rid=None):
    fs = {f['name'].lower(): f for f in db.fields_of(c, cid)}; out = {}; old = db.rec(c, rid)['data'] if rid else {}
    for k, v in (vals or {}).items():
        f = fs.get(str(k).lower())
        if not f: raise ValueError(f"Campo '{k}' não existe. Campos: {', '.join(x['name'] for x in fs.values())}")
        if f['type'] in ('ref', 'refs'):
            tgt = json.loads(f['opts'])['target']; ids = []
            for x in (v if isinstance(v, list) else [v]):
                m = [r['id'] for r in c.execute('select id from records where cid=?', (tgt,)) if str(db.title(c, r['id'])).lower() == str(x).strip().lower()] if not str(x).isdigit() else [int(x)]
                if len(m) != 1: raise ValueError(f"'{x}' {'não encontrado' if not m else 'ambíguo'} em {c.execute('select name from collections where id=?', (tgt,)).fetchone()[0]}; crie ou especifique o registro antes.")
                ids += m
            old_ids = old.get(str(f['id'])) or []; old_ids = [old_ids] if isinstance(old_ids, int) else old_ids
            out[str(f['id'])] = ids if f['type'] == 'ref' else list(dict.fromkeys(old_ids + ids))
        elif f['type'] == 'tags' and rid:
            out[str(f['id'])] = list(dict.fromkeys((old.get(str(f['id'])) or []) + (v if isinstance(v, list) else [v])))
        else: out[str(f['id'])] = v
    return out

def named(c, rid):
    r = db.rec(c, rid); d = {}
    for f in db.fields_of(c, r['cid']):
        v = r['data'].get(str(f['id']))
        if f['type'] == 'ref' and v: v = db.title(c, v)
        elif f['type'] == 'refs': v = [{'id': i, 'titulo': db.title(c, i)} for i in v or []]
        if v not in (None, '', []): d[f['name']] = v
    return {'id': rid, 'titulo': r['title'], 'colecao': c.execute('select name from collections where id=?', (r['cid'],)).fetchone()[0], 'campos': d}

def t_listar():
    with db.tx() as c: return [{'nome': r['name'], 'descricao': r['descr'], 'registros': c.execute('select count(*) from records where cid=?', (r['id'],)).fetchone()[0]} for r in c.execute('select * from collections where archived=0')]
def t_campos(colecao):
    with db.tx() as c: return [{'nome': f['name'], 'tipo': f['type'], 'obrigatorio': bool(f['required']), **json.loads(f['opts'])} for f in db.fields_of(c, col(c, colecao)['id'])]
def t_buscar(consulta, colecao=None):
    with db.tx() as c: return db.search(c, consulta, col(c, colecao)['id'] if colecao else None, 15)
def t_ver(id):
    with db.tx() as c: return named(c, int(id))
def t_criar(colecao, valores):
    with db.tx() as c:
        k = col(c, colecao); rid = db.save(c, k['id'], to_ids(c, k['id'], valores)); return {'criado': True, **db.brief(c, rid)}
def t_atualizar(id, valores):
    with db.tx() as c:
        r = db.rec(c, int(id)); db.save(c, r['cid'], to_ids(c, r['cid'], valores, r['id']), r['id']); return {'atualizado': True, **db.brief(c, r['id'])}
def t_criar_colecao(nome, descricao='', campos=None):
    with db.tx() as c:
        cid = c.execute('insert into collections(name,descr,pos) values(?,?,(select count(*) from collections))', (nome, descricao)).lastrowid
        fs = [{'nome': 'Nome', 'tipo': 'text'}] + [x for x in (campos or []) if str(x.get('nome', '')).lower() != 'nome']
        for i, f in enumerate(fs):
            if f.get('tipo', 'text') not in db.TYPES: raise ValueError(f"Tipo inválido: {f.get('tipo')}")
            c.execute('insert into fields(cid,name,type,required,pos) values(?,?,?,?,?)', (cid, f['nome'], f.get('tipo', 'text'), int(i == 0), i))
        return {'criada': nome}
def t_relacionados(id):
    with db.tx() as c:
        id = int(id); o = []
        for r in c.execute('select r.tid,f.name from refs r join fields f on f.id=r.fid where r.rid=?', (id,)): o.append({**db.brief(c, r[0]), 'via': r[1], 'direcao': 'este registro aponta para'})
        for r in c.execute('select r.rid,f.name from refs r join fields f on f.id=r.fid where r.tid=?', (id,)): o.append({**db.brief(c, r[0]), 'via': r[1], 'direcao': 'aponta para este registro'})
        return o
def t_duplicados(colecao):
    with db.tx() as c:
        g = {}
        for r in c.execute('select id from records where cid=?', (col(c, colecao)['id'],)): g.setdefault(str(db.title(c, r[0])).lower(), []).append(r[0])
        return [{'titulo': k, 'ids': v} for k, v in g.items() if len(v) > 1]
def t_incompletos(colecao):
    with db.tx() as c:
        k = col(c, colecao); fs = db.fields_of(c, k['id']); out = []
        for r in c.execute('select id from records where cid=?', (k['id'],)):
            d = db.rec(c, r[0])['data']; v = [f['name'] for f in fs if d.get(str(f['id'])) in (None, '', [])]
            if v: out.append({**db.brief(c, r[0]), 'vazios': v})
        return out[:30]
def t_excluir(id):
    with db.tx() as c: b = db.brief(c, int(id))
    if not b: raise ValueError('Registro não encontrado')
    return {'aguardando_confirmacao': True, 'aviso': 'Nada foi excluído. Peça ao usuário para confirmar no cartão.', **b}

def t_contar(colecao):
    with db.tx() as c: k = col(c, colecao); return {'colecao': k['name'], 'total': c.execute('select count(*) from records where cid=?', (k['id'],)).fetchone()[0]}
def t_listar_reg(colecao, limite=30):
    with db.tx() as c: k = col(c, colecao); return [db.brief(c, r[0]) for r in c.execute('select id from records where cid=? order by id desc limit ?', (k['id'], int(limite)))]

def schema():
    with db.tx() as c:
        out = []
        for k in c.execute('select * from collections where archived=0').fetchall():
            fs = []
            for f in db.fields_of(c, k['id']):
                t = f['type']; tg = json.loads(f['opts']).get('target'); n = c.execute('select name from collections where id=?', (tg,)).fetchone() if tg else None
                fs.append(f"{f['name']}({t}{'→' + n[0] if n else ''})")
            out.append(f"- {k['name']} ({k['kind']}), {c.execute('select count(*) from records where cid=?', (k['id'],)).fetchone()[0]} registros. Campos: " + ', '.join(fs))
    return '\n'.join(out)

norm = lambda s: ''.join(ch for ch in unicodedata.normalize('NFD', s.lower()) if unicodedata.category(ch) != 'Mn')
def route(text):
    """Consultas determinísticas para intenções comuns: garante dados reais mesmo se o modelo pequeno não chamar ferramentas."""
    t = norm(text).strip(' ?!.'); pre = []
    def run(n, a):
        try: r = IMPL[n](**a)
        except (ValueError, LookupError) as e: r = {'erro': str(e)}
        pre.append({'nome': n, 'args': a, 'resultado': r}); return r
    with db.tx() as c: names = [r['name'] for r in c.execute('select name from collections where archived=0')]
    if re.match(r'quant[oa]s?\b', t):
        for n in names:
            if norm(n) in t: run('contar_registros', {'colecao': n}); break
    else:
        m = re.search(r'(?:o que (?:voce )?sabe(?:s|mos)? sobre|quem e|fale (?:sobre|de)|me fale (?:sobre|de)|informacoes (?:sobre|de))\s+(.+)$', t) or re.search(r'relacionad[oa]s?\s+(?:a|ao|aa|com)\s+(?:o\s+|a\s+)?(?:grupo\s+|interesse\s+)?(.+)$', t) or re.search(r'\b(?:do|no|ao|dos) grupo\s+(.+)$', t)
        if m:
            q = text[m.start(1):m.end(1)].strip(' ?!.'); hits = run('buscar', {'consulta': q})
            if isinstance(hits, list) and hits: run('ver_registro', {'id': hits[0]['id']}); run('relacionados', {'id': hits[0]['id']})
    return pre

def T(n, d, p=None, req=()): return {'type': 'function', 'function': {'name': n, 'description': d, 'parameters': {'type': 'object', 'properties': p or {}, 'required': list(req)}}}
S, O = {'type': 'string'}, {'type': 'object'}
IMPL = {'listar_colecoes': t_listar, 'campos_colecao': t_campos, 'buscar': t_buscar, 'ver_registro': t_ver, 'criar_registro': t_criar, 'atualizar_registro': t_atualizar,
        'criar_colecao': t_criar_colecao, 'relacionados': t_relacionados, 'duplicados': t_duplicados, 'incompletos': t_incompletos, 'contar_registros': t_contar, 'listar_registros': t_listar_reg, 'excluir_registro': t_excluir}
TOOLS = [T('listar_colecoes', 'Lista as coleções e contagens.'), T('campos_colecao', 'Campos de uma coleção.', {'colecao': S}, ['colecao']),
 T('buscar', 'Busca registros por texto em todas as coleções (ou em uma).', {'consulta': S, 'colecao': S}, ['consulta']),
 T('ver_registro', 'Detalhes completos de um registro pelo id.', {'id': {'type': 'integer'}}, ['id']),
 T('criar_registro', 'Cria registro. valores: {nome do campo: valor}. Referências aceitam o título do registro existente.', {'colecao': S, 'valores': O}, ['colecao', 'valores']),
 T('atualizar_registro', 'Altera campos de um registro; em campos de referência múltipla/tags os valores são ADICIONADOS.', {'id': {'type': 'integer'}, 'valores': O}, ['id', 'valores']),
 T('criar_colecao', 'Cria coleção. campos: [{nome, tipo}] (tipos: ' + ','.join(db.TYPES) + ').', {'nome': S, 'descricao': S, 'campos': {'type': 'array', 'items': O}}, ['nome']),
 T('relacionados', 'Registros ligados por referência a um registro (nos dois sentidos).', {'id': {'type': 'integer'}}, ['id']),
 T('duplicados', 'Títulos duplicados em uma coleção.', {'colecao': S}, ['colecao']), T('incompletos', 'Registros com campos vazios.', {'colecao': S}, ['colecao']),
 T('contar_registros', 'Conta registros de uma coleção.', {'colecao': S}, ['colecao']), T('listar_registros', 'Lista os registros mais recentes de uma coleção.', {'colecao': S, 'limite': {'type': 'integer'}}, ['colecao']),
 T('excluir_registro', 'Solicita exclusão de um registro (exige confirmação do usuário).', {'id': {'type': 'integer'}}, ['id'])]

def history(n=16):
    with db.tx() as c: rows = c.execute('select role,content,meta from chat order by id desc limit ?', (n,)).fetchall()
    out = []
    for r in reversed(rows):
        txt = r[1]
        if r[0] == 'assistant' and r[2]:
            cit = {}
            for u in json.loads(r[2]).get('tools', []):
                R = u['resultado']
                for x in (R if isinstance(R, list) else [R]):
                    if isinstance(x, dict) and x.get('id') and x.get('titulo'): cit[x['id']] = f"{x['titulo']} (id {x['id']}, {x.get('colecao', '')})"
            if cit: txt += '\n[Registros consultados: ' + '; '.join(list(cit.values())[:10]) + ']'
        out.append({'role': r[0], 'content': txt})
    return out

def chat(text):
    pre = route(text); inj = ('\n\n[Dados já consultados no banco para esta pergunta; responda com base neles]\n' + json.dumps([{'ferramenta': p['nome'], 'resultado': p['resultado']} for p in pre], ensure_ascii=False)[:3500]) if pre else ''
    msgs = [{'role': 'system', 'content': SYS + '\n\nEstrutura atual do GHOST:\n' + schema()}] + history() + [{'role': 'user', 'content': text + inj}]; used, confirm, reply = list(pre), [], None
    for _ in range(6):
        m = call('/api/chat', {'model': db.setting('model'), 'messages': msgs, 'tools': TOOLS, 'stream': False, 'options': {'temperature': 0.2, 'num_ctx': 8192}})['message']
        msgs.append(m)
        if not m.get('tool_calls'): reply = m.get('content', ''); break
        for tc in m['tool_calls']:
            fn = tc['function']; a = fn.get('arguments') or {}
            a = json.loads(a) if isinstance(a, str) else a
            try: res = IMPL[fn['name']](**a)
            except KeyError: res = {'erro': 'ferramenta desconhecida'}
            except (ValueError, LookupError, TypeError) as e: res = {'erro': str(e)}
            if isinstance(res, dict) and res.get('aguardando_confirmacao'): confirm.append(res)
            used.append({'nome': fn['name'], 'args': a, 'resultado': res}); msgs.append({'role': 'tool', 'content': json.dumps(res, ensure_ascii=False)[:5000]})
    reply = reply or 'Não consegui concluir a solicitação com as ferramentas disponíveis.'
    meta = json.dumps({'tools': used, 'confirm': confirm}, ensure_ascii=False)
    with db.tx() as c:
        c.execute('insert into chat(role,content,ts) values(?,?,?)', ('user', text, db.now()))
        c.execute('insert into chat(role,content,meta,ts) values(?,?,?,?)', ('assistant', reply, meta, db.now()))
    return {'reply': reply, 'tools': used, 'confirm': confirm}
