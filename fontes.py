"""Fontes de documentos de cada índice (os *seeds* das coleções do Watson
Explorer). Uma fonte diz de onde o crawler (crawler.py) busca documentos e de
quanto em quanto tempo. Nesta primeira versão só existe o tipo `pasta`: uma
pasta do servidor — normalmente um compartilhamento de rede montado no
container pelo Dokploy.

Tabelas no MySQL (criadas sozinhas no primeiro uso):
- `fontes`: a configuração e a agenda de cada fonte;
- `fonte_itens`: o que a fonte tinha na última passada (doc_id, caminho e
  assinatura = data de modificação + tamanho), usado para descobrir o que é novo, mudou ou sumiu;
- `fonte_execucoes`: o histórico de cada passada, com as contagens.
"""

import fnmatch
import json
import os
import re
import threading

import config
import indices
from db import conectar

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS fontes (
        id               BIGINT AUTO_INCREMENT PRIMARY KEY,
        nome             VARCHAR(100)  NOT NULL UNIQUE,
        indice           VARCHAR(255)  NOT NULL,
        tipo             VARCHAR(20)   NOT NULL,
        config           TEXT          NOT NULL,
        intervalo_horas  INT           NOT NULL DEFAULT 0,
        ativa            TINYINT(1)    NOT NULL DEFAULT 1,
        solicitada       TINYINT(1)    NOT NULL DEFAULT 0,
        proxima_em       DATETIME      NULL,
        criado_em        DATETIME      NOT NULL,
        INDEX idx_indice (indice)
    ) CHARACTER SET utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS fonte_itens (
        fonte_id    BIGINT        NOT NULL,
        doc_id      CHAR(64)      NOT NULL,
        chave       VARCHAR(1024) NOT NULL,
        assinatura  VARCHAR(100)  NOT NULL,
        visto_em    DATETIME      NOT NULL,
        PRIMARY KEY (fonte_id, doc_id),
        FOREIGN KEY (fonte_id) REFERENCES fontes (id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS fonte_execucoes (
        id            BIGINT AUTO_INCREMENT PRIMARY KEY,
        fonte_id      BIGINT        NOT NULL,
        status        VARCHAR(20)   NOT NULL,
        encontrados   INT           NOT NULL DEFAULT 0,
        novos         INT           NOT NULL DEFAULT 0,
        alterados     INT           NOT NULL DEFAULT 0,
        removidos     INT           NOT NULL DEFAULT 0,
        ja_na_fila    INT           NOT NULL DEFAULT 0,
        mensagem      TEXT          NULL,
        iniciado_em   DATETIME      NOT NULL,
        finalizado_em DATETIME      NULL,
        INDEX idx_fonte (fonte_id, id),
        FOREIGN KEY (fonte_id) REFERENCES fontes (id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4
    """,
]

TIPOS = {"pasta": "Pasta / compartilhamento de rede"}
INTERVALOS = [(0, "Só manual"), (1, "A cada hora"), (6, "A cada 6 horas"), (12, "A cada 12 horas"), (24, "Uma vez por dia"), (168, "Uma vez por semana")]
IGNORAR_PADRAO = "~$*, .*, Thumbs.db, desktop.ini"
LIMITE_NOME = 100
LIMITE_IGNORAR = 500

_tabelas_ok = False
_trava = threading.Lock()


def _garantir_tabelas(con):
    global _tabelas_ok
    with _trava:
        if _tabelas_ok:
            return
        with con.cursor() as cur:
            for ddl in _DDL:
                cur.execute(ddl)
        _tabelas_ok = True


def conectar_fontes():
    con = conectar()
    _garantir_tabelas(con)
    return con


def raizes_permitidas():
    """Pastas do servidor onde uma fonte pode apontar (e suas subpastas).
    Sem isso, quem alcança a administração indexaria /etc ou o código do app."""
    return [os.path.realpath(r.strip()) for r in config.FONTES_PASTAS_PERMITIDAS.split(",") if r.strip()]


def pasta_permitida(caminho):
    real = os.path.realpath(caminho)
    for raiz in raizes_permitidas():
        try:
            if os.path.commonpath([real, raiz]) == raiz:
                return True
        except ValueError:  # discos diferentes no Windows
            continue
    return False


def separar_padroes(texto):
    return [p.strip() for p in re.split(r"[,;\n]", texto or "") if p.strip()]


def ignorado(nome, padroes):
    return any(fnmatch.fnmatch(nome.lower(), p.lower()) for p in padroes)


def validar(nome, indice, caminho, extensoes, ignorar, subpastas, intervalo):
    """Valida o formulário e devolve os campos prontos para gravar. Levanta
    ValueError com a mensagem para a tela."""
    nome = re.sub(r"\s+", " ", nome or "").strip()
    if not nome:
        raise ValueError("Informe o nome da fonte.")
    if len(nome) > LIMITE_NOME:
        raise ValueError(f"O nome pode ter até {LIMITE_NOME} caracteres.")
    if not indices.eh_gerenciado(indice):
        raise ValueError("Escolha um índice válido.")
    caminho = (caminho or "").strip()
    if not caminho:
        raise ValueError("Informe a pasta.")
    if not os.path.isabs(caminho):
        raise ValueError("Use o caminho completo da pasta no servidor (ex.: /mnt/fontes/juridico).")
    if not pasta_permitida(caminho):
        raise ValueError(
            "Essa pasta está fora das pastas liberadas para fontes ("
            + ", ".join(raizes_permitidas()) + "). Ajuste FONTES_PASTAS_PERMITIDAS no Dokploy se precisar."
        )
    validas = sorted(e.lstrip(".") for e in config.EXTENSOES_VALIDAS)
    extensoes = [e for e in extensoes if e in validas]
    if not extensoes:
        raise ValueError("Marque pelo menos um tipo de arquivo.")
    if len(ignorar or "") > LIMITE_IGNORAR:
        raise ValueError(f"Os padrões a ignorar podem ter até {LIMITE_IGNORAR} caracteres.")
    try:
        intervalo = int(intervalo)
    except (TypeError, ValueError):
        intervalo = 0
    if intervalo not in {h for h, _ in INTERVALOS}:
        intervalo = 0
    conf = {"caminho": caminho, "extensoes": extensoes, "ignorar": ", ".join(separar_padroes(ignorar)), "subpastas": bool(subpastas)}
    return nome, indice, conf, intervalo


def _linha(f):
    if f:
        f["config"] = json.loads(f["config"])
        f["intervalo_texto"] = dict(INTERVALOS).get(f["intervalo_horas"], f"A cada {f['intervalo_horas']} h")
    return f


def listar():
    """Fontes com a última execução de cada uma (`ultima`, ou None)."""
    with conectar_fontes() as con, con.cursor() as cur:
        cur.execute("SELECT * FROM fontes ORDER BY nome")
        lista = [_linha(f) for f in cur.fetchall()]
        cur.execute(
            "SELECT e.* FROM fonte_execucoes e JOIN "
            "(SELECT fonte_id, MAX(id) AS id FROM fonte_execucoes GROUP BY fonte_id) u ON u.id = e.id"
        )
        ultimas = {e["fonte_id"]: e for e in cur.fetchall()}
        cur.execute("SELECT fonte_id, COUNT(*) AS n FROM fonte_itens GROUP BY fonte_id")
        itens = {linha["fonte_id"]: linha["n"] for linha in cur.fetchall()}
    for f in lista:
        f["ultima"] = ultimas.get(f["id"])
        f["itens"] = itens.get(f["id"], 0)
    return lista


def obter(fonte_id):
    with conectar_fontes() as con, con.cursor() as cur:
        cur.execute("SELECT * FROM fontes WHERE id = %s", (fonte_id,))
        return _linha(cur.fetchone())


def execucoes(fonte_id, limite=10):
    with conectar_fontes() as con, con.cursor() as cur:
        cur.execute("SELECT * FROM fonte_execucoes WHERE fonte_id = %s ORDER BY id DESC LIMIT %s", (fonte_id, limite))
        return cur.fetchall()


def criar(nome, indice, conf, intervalo):
    try:
        with conectar_fontes() as con, con.cursor() as cur:
            cur.execute(
                "INSERT INTO fontes (nome, indice, tipo, config, intervalo_horas, ativa, solicitada, criado_em)"
                " VALUES (%s, %s, 'pasta', %s, %s, 1, 1, UTC_TIMESTAMP())",  # a primeira passada sai logo
                (nome, indice, json.dumps(conf, ensure_ascii=False), intervalo),
            )
            return cur.lastrowid
    except Exception as e:
        if "Duplicate" in str(e):
            raise ValueError("Já existe uma fonte com esse nome.")
        raise


def atualizar(fonte_id, nome, conf, intervalo, ativa):
    """O índice não muda depois de criada (os documentos já estão nele)."""
    try:
        with conectar_fontes() as con, con.cursor() as cur:
            cur.execute(
                "UPDATE fontes SET nome = %s, config = %s, intervalo_horas = %s, ativa = %s,"
                " proxima_em = IF(%s > 0, UTC_TIMESTAMP() + INTERVAL %s HOUR, NULL) WHERE id = %s",
                (nome, json.dumps(conf, ensure_ascii=False), intervalo, 1 if ativa else 0, intervalo, intervalo, fonte_id),
            )
    except Exception as e:
        if "Duplicate" in str(e):
            raise ValueError("Já existe uma fonte com esse nome.")
        raise


def solicitar(fonte_id):
    """"Rodar agora": o agendador pega na próxima volta (em segundos)."""
    with conectar_fontes() as con, con.cursor() as cur:
        cur.execute("UPDATE fontes SET solicitada = 1 WHERE id = %s", (fonte_id,))


def doc_ids(fonte_id):
    with conectar_fontes() as con, con.cursor() as cur:
        cur.execute("SELECT doc_id FROM fonte_itens WHERE fonte_id = %s", (fonte_id,))
        return [linha["doc_id"] for linha in cur.fetchall()]


def apagar(fonte_id):
    with conectar_fontes() as con, con.cursor() as cur:
        cur.execute("DELETE FROM fontes WHERE id = %s", (fonte_id,))


def apagar_do_indice(indice):
    """Chamado ao apagar um índice: as fontes dele não têm mais para onde indexar."""
    with conectar_fontes() as con, con.cursor() as cur:
        cur.execute("DELETE FROM fontes WHERE indice = %s", (indice,))
