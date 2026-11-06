"""Pastas de trabalho: o usuário guarda documentos achados na busca em pastas
privadas (só ele vê) ou públicas (todo mundo vê), como no Watson Explorer.

Só o dono mexe na pasta (adicionar, tirar, renomear, apagar); a
administração também pode, para limpar pastas abandonadas. A pasta guarda o
nome e o título do documento na hora em que foi salvo, então continua
legível mesmo se o documento sair do índice depois.

Enquanto o login não entra, o dono de toda pasta é `None` (anônimo): as
privadas não são de fato privadas. Com o login, `acesso.usuario_atual()`
passa a devolver quem está logado e a regra vale de verdade.

Tabelas `pastas` e `pasta_itens` no MySQL, criadas sozinhas no primeiro uso.
"""

import re
import threading

from db import conectar

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS pastas (
        id            BIGINT AUTO_INCREMENT PRIMARY KEY,
        dono          VARCHAR(255) NULL,
        nome          VARCHAR(100) NOT NULL,
        descricao     VARCHAR(500) NULL,
        publica       TINYINT(1)   NOT NULL DEFAULT 0,
        criado_em     DATETIME     NOT NULL,
        atualizado_em DATETIME     NOT NULL,
        INDEX idx_dono (dono),
        INDEX idx_publica (publica)
    ) CHARACTER SET utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS pasta_itens (
        pasta_id       BIGINT        NOT NULL,
        indice         VARCHAR(255)  NOT NULL,
        doc_id         VARCHAR(128)  NOT NULL,
        nome           VARCHAR(512)  NOT NULL,
        titulo         VARCHAR(512)  NULL,
        nota           VARCHAR(500)  NULL,
        adicionado_por VARCHAR(255)  NULL,
        adicionado_em  DATETIME      NOT NULL,
        PRIMARY KEY (pasta_id, indice, doc_id),
        INDEX idx_indice (indice),
        FOREIGN KEY (pasta_id) REFERENCES pastas (id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4
    """,
]

LIMITE_NOME = 100
LIMITE_DESCRICAO = 500
LIMITE_NOTA = 500

_tabelas_ok = False
_trava = threading.Lock()


def _conectar():
    global _tabelas_ok
    con = conectar()
    with _trava:
        if not _tabelas_ok:
            with con.cursor() as cur:
                for ddl in _DDL:
                    cur.execute(ddl)
            _tabelas_ok = True
    return con


def _limpar(texto, limite, campo):
    texto = re.sub(r"\s+", " ", texto or "").strip()
    if len(texto) > limite:
        raise ValueError(f"{campo} pode ter até {limite} caracteres.")
    return texto


def validar(nome, descricao):
    nome = _limpar(nome, LIMITE_NOME, "O nome")
    if not nome:
        raise ValueError("Dê um nome para a pasta.")
    return nome, (_limpar(descricao, LIMITE_DESCRICAO, "A descrição") or None)


def pode_ver(pasta, usuario):
    return bool(pasta) and (pasta["publica"] or pasta["dono"] == usuario)


def pode_editar(pasta, usuario, admin=False):
    return bool(pasta) and (admin or pasta["dono"] == usuario)


_COLUNAS = ("SELECT p.*, (SELECT COUNT(*) FROM pasta_itens i WHERE i.pasta_id = p.id) AS itens FROM pastas p")


def listar(usuario):
    """(minhas, públicas de outras pessoas), cada uma com a contagem de itens."""
    with _conectar() as con, con.cursor() as cur:
        cur.execute(f"{_COLUNAS} WHERE p.dono <=> %s ORDER BY p.nome", (usuario,))
        minhas = cur.fetchall()
        cur.execute(f"{_COLUNAS} WHERE p.publica = 1 AND NOT (p.dono <=> %s) ORDER BY p.nome", (usuario,))
        publicas = cur.fetchall()
    return minhas, publicas


LIMITE_TODAS = 500


def todas(dono=None, sem_dono=False):
    """Pastas de todo mundo, mais recentes primeiro (visão do administrador).
    Com `dono`, só as dessa pessoa; com `sem_dono`, só as criadas antes do
    login (dono vazio). No máximo LIMITE_TODAS."""
    sql, params = _COLUNAS, ()
    if sem_dono:
        sql += " WHERE p.dono IS NULL"
    elif dono:
        sql, params = sql + " WHERE p.dono = %s", (dono,)
    with _conectar() as con, con.cursor() as cur:
        cur.execute(f"{sql} ORDER BY p.atualizado_em DESC LIMIT {LIMITE_TODAS}", params)
        return cur.fetchall()


def editaveis(usuario, indice=None, doc_id=None):
    """Pastas onde o usuário pode guardar documentos, marcando as que já
    têm o documento (para o popup "Salvar na pasta")."""
    with _conectar() as con, con.cursor() as cur:
        cur.execute(
            "SELECT p.id, p.nome, p.publica, EXISTS(SELECT 1 FROM pasta_itens i WHERE i.pasta_id = p.id"
            " AND i.indice = %s AND i.doc_id = %s) AS contem FROM pastas p WHERE p.dono <=> %s ORDER BY p.atualizado_em DESC",
            (indice or "", doc_id or "", usuario),
        )
        return cur.fetchall()


def obter(pasta_id):
    with _conectar() as con, con.cursor() as cur:
        cur.execute(f"{_COLUNAS} WHERE p.id = %s", (pasta_id,))
        return cur.fetchone()


def itens(pasta_id):
    with _conectar() as con, con.cursor() as cur:
        cur.execute("SELECT * FROM pasta_itens WHERE pasta_id = %s ORDER BY adicionado_em DESC", (pasta_id,))
        return cur.fetchall()


def criar(usuario, nome, descricao, publica):
    nome, descricao = validar(nome, descricao)
    with _conectar() as con, con.cursor() as cur:
        cur.execute(
            "INSERT INTO pastas (dono, nome, descricao, publica, criado_em, atualizado_em)"
            " VALUES (%s, %s, %s, %s, UTC_TIMESTAMP(), UTC_TIMESTAMP())",
            (usuario, nome, descricao, 1 if publica else 0),
        )
        return cur.lastrowid


def atualizar(pasta_id, nome, descricao, publica):
    nome, descricao = validar(nome, descricao)
    with _conectar() as con, con.cursor() as cur:
        cur.execute(
            "UPDATE pastas SET nome = %s, descricao = %s, publica = %s, atualizado_em = UTC_TIMESTAMP() WHERE id = %s",
            (nome, descricao, 1 if publica else 0, pasta_id),
        )


def apagar(pasta_id):
    with _conectar() as con, con.cursor() as cur:
        cur.execute("DELETE FROM pastas WHERE id = %s", (pasta_id,))


def adicionar(pasta_id, indice, doc_id, nome, titulo, usuario, nota=None):
    """Guarda o documento na pasta (se já estiver, só atualiza nome/título)."""
    nota = _limpar(nota, LIMITE_NOTA, "A nota") or None
    with _conectar() as con, con.cursor() as cur:
        cur.execute(
            "INSERT INTO pasta_itens (pasta_id, indice, doc_id, nome, titulo, nota, adicionado_por, adicionado_em)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP())"
            " ON DUPLICATE KEY UPDATE nome = VALUES(nome), titulo = VALUES(titulo), nota = COALESCE(VALUES(nota), nota)",
            (pasta_id, indice, doc_id, (nome or "(sem nome)")[:512], (titulo or "")[:512] or None, nota, usuario),
        )
        cur.execute("UPDATE pastas SET atualizado_em = UTC_TIMESTAMP() WHERE id = %s", (pasta_id,))


def remover(pasta_id, indice, doc_id):
    with _conectar() as con, con.cursor() as cur:
        cur.execute("DELETE FROM pasta_itens WHERE pasta_id = %s AND indice = %s AND doc_id = %s", (pasta_id, indice, doc_id))
        cur.execute("UPDATE pastas SET atualizado_em = UTC_TIMESTAMP() WHERE id = %s", (pasta_id,))


def anotar(pasta_id, indice, doc_id, nota):
    nota = _limpar(nota, LIMITE_NOTA, "A nota") or None
    with _conectar() as con, con.cursor() as cur:
        cur.execute("UPDATE pasta_itens SET nota = %s WHERE pasta_id = %s AND indice = %s AND doc_id = %s", (nota, pasta_id, indice, doc_id))


def remover_do_indice(indice):
    """Chamado ao apagar um índice: os documentos dele não existem mais."""
    with _conectar() as con, con.cursor() as cur:
        cur.execute("DELETE FROM pasta_itens WHERE indice = %s", (indice,))
