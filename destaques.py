"""Destaques (best bets / spotlights do Watson Explorer): o administrador
escolhe um documento e as palavras-chave que o trazem para o topo da busca.

Um destaque aparece num bloco próprio acima dos resultados, só na primeira
página, quando a consulta é uma das palavras-chave ou as contém como
palavras inteiras (sem diferenciar maiúsculas nem acentos). Não depende dos
filtros nem da ordenação. Na busca de um projeto, só aparece se o índice
do documento faz parte do projeto.

Fica no MySQL (tabela `destaques`, criada sozinha no primeiro uso) e é lido
com um cache curto, como o dicionário.
"""

import re
import threading
import time

from db import conectar
from dicionario import normalizar, separar_sinonimos

_DDL = """
CREATE TABLE IF NOT EXISTS destaques (
    id        BIGINT AUTO_INCREMENT PRIMARY KEY,
    termos    VARCHAR(500) NOT NULL,
    indice    VARCHAR(255) NOT NULL,
    doc_id    VARCHAR(128) NOT NULL,
    nome      VARCHAR(512) NULL,
    descricao VARCHAR(300) NULL,
    criado_em DATETIME     NOT NULL,
    INDEX idx_indice (indice)
) CHARACTER SET utf8mb4
"""

LIMITE_TERMO = 100
LIMITE_TERMOS = 500
LIMITE_DESCRICAO = 300
MAX_TERMOS = 20
MAX_NA_TELA = 3
CACHE_SEGUNDOS = 30

_tabela_ok = False
_trava = threading.Lock()
_cache = {"quando": 0.0, "entradas": []}


def _garantir_tabela(con):
    global _tabela_ok
    with _trava:
        if _tabela_ok:
            return
        with con.cursor() as cur:
            cur.execute(_DDL)
        _tabela_ok = True


def _conectar():
    con = conectar()
    _garantir_tabela(con)
    return con


def _validar(termos, descricao):
    formas = separar_sinonimos(termos)
    if not formas:
        raise ValueError("Informe pelo menos uma palavra-chave.")
    if len(formas) > MAX_TERMOS:
        raise ValueError(f"Use no máximo {MAX_TERMOS} palavras-chave.")
    if any(len(f) > LIMITE_TERMO for f in formas):
        raise ValueError(f"Cada palavra-chave pode ter até {LIMITE_TERMO} caracteres.")
    texto = ", ".join(formas)
    if len(texto) > LIMITE_TERMOS:
        raise ValueError(f"As palavras-chave juntas podem ter até {LIMITE_TERMOS} caracteres.")
    descricao = re.sub(r"\s+", " ", descricao or "").strip()
    if len(descricao) > LIMITE_DESCRICAO:
        raise ValueError(f"A descrição pode ter até {LIMITE_DESCRICAO} caracteres.")
    return texto, (descricao or None)


def invalidar_cache():
    _cache["quando"] = 0.0


def listar():
    with _conectar() as con, con.cursor() as cur:
        cur.execute("SELECT id, termos, indice, doc_id, nome, descricao FROM destaques ORDER BY termos")
        return cur.fetchall()


def obter(destaque_id):
    with _conectar() as con, con.cursor() as cur:
        cur.execute("SELECT id, termos, indice, doc_id, nome, descricao FROM destaques WHERE id = %s", (destaque_id,))
        return cur.fetchone()


def criar(termos, descricao, indice, doc_id, nome):
    termos, descricao = _validar(termos, descricao)
    with _conectar() as con, con.cursor() as cur:
        cur.execute(
            "INSERT INTO destaques (termos, indice, doc_id, nome, descricao, criado_em)"
            " VALUES (%s, %s, %s, %s, %s, UTC_TIMESTAMP())",
            (termos, indice, doc_id, (nome or "")[:512] or None, descricao),
        )
    invalidar_cache()


def atualizar(destaque_id, termos, descricao):
    termos, descricao = _validar(termos, descricao)
    with _conectar() as con, con.cursor() as cur:
        cur.execute("UPDATE destaques SET termos = %s, descricao = %s WHERE id = %s", (termos, descricao, destaque_id))
    invalidar_cache()


def apagar(destaque_id):
    with _conectar() as con, con.cursor() as cur:
        cur.execute("DELETE FROM destaques WHERE id = %s", (destaque_id,))
    invalidar_cache()


def apagar_do_indice(indice):
    """Chamado ao apagar um índice: os documentos dele não existem mais."""
    with _conectar() as con, con.cursor() as cur:
        cur.execute("DELETE FROM destaques WHERE indice = %s", (indice,))
    invalidar_cache()


def _entradas_em_cache():
    """Entradas do cache; se o banco falhar, usa o cache velho (ou nada)."""
    if time.monotonic() - _cache["quando"] > CACHE_SEGUNDOS:
        try:
            _cache["entradas"] = listar()
        except Exception as e:
            print(f"[Destaques] Não consegui ler os destaques: {e}")
        _cache["quando"] = time.monotonic()
    return _cache["entradas"]


def casam(consulta, indice_permitido=lambda indice: True, entradas=None):
    """Destaques cuja palavra-chave é a consulta ou aparece nela como
    palavras inteiras, de índices aceitos por `indice_permitido`. Sem
    repetir documento, no máximo MAX_NA_TELA."""
    c = normalizar(consulta)
    if not c:
        return []
    entradas = _entradas_em_cache() if entradas is None else entradas
    achados, vistos = [], set()
    for e in entradas:
        chave = (e["indice"], e["doc_id"])
        if chave in vistos or not indice_permitido(e["indice"]):
            continue
        for termo in separar_sinonimos(e["termos"]):
            t = normalizar(termo)
            if t and re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", c):
                # consulta igual à palavra-chave vem antes de "contém"
                achados.append((0 if t == c else 1, e))
                vistos.add(chave)
                break
    achados.sort(key=lambda par: par[0])
    return [e for _, e in achados[:MAX_NA_TELA]]
