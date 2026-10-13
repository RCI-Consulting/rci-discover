"""Dicionário de termos: vocabulário da empresa (siglas, nomes, sinônimos)
mantido na administração, como os dicionários do Watson Explorer.

Cada entrada tem um termo preferido e sinônimos/siglas equivalentes. Serve a:
- **autocomplete**: o termo preferido aparece nas sugestões enquanto se digita
  (por qualquer forma: digitar a sigla sugere o nome completo);
- **expansão da busca**: se a consulta contém uma das formas, a busca também
  procura pelas outras (ex.: "NDA" também acha "acordo de confidencialidade").
  É feita na hora da busca, sem reindexar nada.

Fica no MySQL (tabela `dicionario_termos`, criada sozinha no primeiro uso) e
é lido com um cache curto, para a busca não consultar o banco a cada tecla.
"""

import re
import threading
import time
import unicodedata

import pymysql

from db import conectar

_DDL = """
CREATE TABLE IF NOT EXISTS dicionario_termos (
    id        BIGINT AUTO_INCREMENT PRIMARY KEY,
    termo     VARCHAR(100) NOT NULL UNIQUE,
    sinonimos VARCHAR(500) NULL,
    criado_em DATETIME     NOT NULL
) CHARACTER SET utf8mb4
"""

LIMITE_TERMO = 100
LIMITE_SINONIMOS = 500
MAX_FORMAS = 20          # sinônimos por entrada
MAX_ALTERNATIVAS = 6     # consultas extras geradas por uma busca
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


def normalizar(texto):
    """Minúsculo, sem acento e com espaços simples: para comparar prefixos."""
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sem_acento.casefold()).strip()


def separar_sinonimos(texto):
    """'nda; termo de sigilo, NDA' -> ['nda', 'termo de sigilo'] (sem repetição, na ordem)."""
    vistos, formas = set(), []
    for parte in re.split(r"[,;\n]", texto or ""):
        parte = re.sub(r"\s+", " ", parte).strip()
        chave = parte.casefold()
        if parte and chave not in vistos:
            vistos.add(chave)
            formas.append(parte)
    return formas


def _validar(termo, sinonimos):
    termo = re.sub(r"\s+", " ", termo or "").strip()
    if not termo:
        raise ValueError("Informe o termo.")
    if len(termo) > LIMITE_TERMO:
        raise ValueError(f"O termo pode ter até {LIMITE_TERMO} caracteres.")
    formas = [f for f in separar_sinonimos(sinonimos) if f.casefold() != termo.casefold()]
    if len(formas) > MAX_FORMAS:
        raise ValueError(f"Use no máximo {MAX_FORMAS} sinônimos por termo.")
    if any(len(f) > LIMITE_TERMO for f in formas):
        raise ValueError(f"Cada sinônimo pode ter até {LIMITE_TERMO} caracteres.")
    texto = ", ".join(formas)
    if len(texto) > LIMITE_SINONIMOS:
        raise ValueError(f"Os sinônimos juntos podem ter até {LIMITE_SINONIMOS} caracteres.")
    return termo, (texto or None)


def invalidar_cache():
    _cache["quando"] = 0.0


def listar():
    """Todas as entradas, com a lista `formas` (termo preferido + sinônimos)."""
    with _conectar() as con, con.cursor() as cur:
        cur.execute("SELECT id, termo, sinonimos FROM dicionario_termos ORDER BY termo")
        entradas = cur.fetchall()
    for e in entradas:
        e["formas"] = [e["termo"]] + separar_sinonimos(e["sinonimos"])
    return entradas


def _entradas_em_cache():
    """Entradas do cache; se o banco falhar, usa o cache velho (ou nada)."""
    if time.monotonic() - _cache["quando"] > CACHE_SEGUNDOS:
        try:
            _cache["entradas"] = listar()
        except Exception as e:
            print(f"[Dicionário] Não consegui ler o dicionário: {e}")
        _cache["quando"] = time.monotonic()
    return _cache["entradas"]


def obter(entrada_id):
    with _conectar() as con, con.cursor() as cur:
        cur.execute("SELECT id, termo, sinonimos FROM dicionario_termos WHERE id = %s", (entrada_id,))
        return cur.fetchone()


def criar(termo, sinonimos):
    termo, sinonimos = _validar(termo, sinonimos)
    try:
        with _conectar() as con, con.cursor() as cur:
            cur.execute(
                "INSERT INTO dicionario_termos (termo, sinonimos, criado_em) VALUES (%s, %s, UTC_TIMESTAMP())",
                (termo, sinonimos),
            )
    except pymysql.err.IntegrityError:
        raise ValueError("Esse termo já está no dicionário.")
    invalidar_cache()


def atualizar(entrada_id, termo, sinonimos):
    termo, sinonimos = _validar(termo, sinonimos)
    try:
        with _conectar() as con, con.cursor() as cur:
            cur.execute(
                "UPDATE dicionario_termos SET termo = %s, sinonimos = %s WHERE id = %s",
                (termo, sinonimos, entrada_id),
            )
    except pymysql.err.IntegrityError:
        raise ValueError("Esse termo já está no dicionário.")
    invalidar_cache()


def apagar(entrada_id):
    with _conectar() as con, con.cursor() as cur:
        cur.execute("DELETE FROM dicionario_termos WHERE id = %s", (entrada_id,))
        apagou = cur.rowcount == 1
    invalidar_cache()
    return apagou


def expandir(consulta, entradas=None):
    """Consultas alternativas: para cada forma do dicionário que aparece na
    consulta (palavra inteira), uma cópia com ela trocada por cada forma
    equivalente. Devolve [{"consulta", "de", "para"}], no máximo MAX_ALTERNATIVAS."""
    consulta = (consulta or "").strip()
    if not consulta:
        return []
    entradas = _entradas_em_cache() if entradas is None else entradas
    alternativas = []
    for entrada in entradas:
        formas = entrada["formas"]
        for forma in formas:
            padrao = re.compile(r"(?<!\w)" + re.escape(forma) + r"(?!\w)", re.IGNORECASE)
            if not padrao.search(consulta):
                continue
            for outra in formas:
                if outra.casefold() == forma.casefold():
                    continue
                alternativas.append({
                    "consulta": padrao.sub(lambda _m, o=outra: o, consulta, count=1),
                    "de": forma,
                    "para": outra,
                })
            break  # uma forma por entrada basta: as demais são equivalentes
    unicas, vistas = [], {consulta.casefold()}
    for alt in alternativas:
        chave = alt["consulta"].casefold()
        if chave not in vistas:
            vistas.add(chave)
            unicas.append(alt)
    return unicas[:MAX_ALTERNATIVAS]


def sugerir(prefixo, limite=3, entradas=None):
    """Termos preferidos cujas formas (ou palavras delas) começam com o que o
    usuário digitou. Sem acento e sem diferenciar maiúsculas."""
    p = normalizar(prefixo)
    if len(p) < 2:
        return []
    entradas = _entradas_em_cache() if entradas is None else entradas
    achados = []
    for entrada in entradas:
        for forma in entrada["formas"]:
            f = normalizar(forma)
            if f.startswith(p) or any(palavra.startswith(p) for palavra in f.split()):
                achados.append((0 if f.startswith(p) else 1, entrada["termo"]))
                break
    achados.sort()
    return [termo for _, termo in achados[:limite]]
