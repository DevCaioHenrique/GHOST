# GHOST 1.0
Central local de conhecimento pessoal (Flask + SQLite + JS puro) com assistente de IA via Ollama.

## Instalação
1. Python 3.10+ e `pip install -r requirements.txt`
2. Ollama: `ollama pull qwen2.5:3b` e deixe o serviço ativo (`ollama serve`)
3. `python app.py` e abra http://127.0.0.1:5000 (no Windows: `iniciar_ghost.bat`)

O banco `data/ghost.db` é criado e migrado automaticamente. Modelo e endereço do Ollama ficam em Configurações.

## Estrutura
db.py (schema, migrações, validação, CRUD) · ai.py (Ollama + ferramentas controladas) · app.py (API JSON) · static/ (interface)
