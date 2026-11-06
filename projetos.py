"""Projetos: um agrupamento de índices com página de busca própria
(/p/<slug>), como os projetos do Watson Explorer.

Um índice pode estar em vários projetos. O vínculo guarda o nome real do
índice no OpenSearch (ex.: `rci-contratos`); índice que deixou de existir é
simplesmente ignorado na busca. Tudo fica no MySQL, nas tabelas `projetos` e
`projeto_indices` (criadas sozinhas no primeiro uso, como a fila e a auditoria).
"""

import re
import threading
import unicodedata

import indices
from db import conectar

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS projetos (
        id        BIGINT AUTO_INCREMENT PRIMARY KEY,
        slug      VARCHAR(50)  NOT NULL UNIQUE,
        nome      VARCHAR(100) NOT NULL,
        descricao VARCHAR(500) NULL,
        criado_em DATETIME     NOT NULL
    ) CHARACTER SET utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS projeto_indices (
        projeto_id BIGINT       NOT NULL,
        indice     VARCHAR(255) NOT NULL,
        PRIMARY KEY (projeto_id, indice),
        INDEX idx_indice (indice),
        FOREIGN KEY (projeto_id) REFERENCES projetos (id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4
    """,
    # Quem (além dos administradores, que veem tudo) acessa cada projeto.
    """
    CREATE TABLE IF NOT EXISTS projeto_usuarios (
        projeto_id BIGINT NOT NULL,
        usuario_id BIGINT NOT NULL,
        PRIMARY KEY (projeto_id, usuario_id),
        INDEX idx_usuario (usuario_id),
        FOREIGN KEY (projeto_id) REFERENCES projetos (id) ON DELETE CASCADE
    ) CHARACTER SET utf8mb4
    """,
]

_SLUG_VALIDO = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")
LIMITE_NOME = 100
LIMITE_DESCRICAO = 500

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


def _conectar():
    con = conectar()
    _garantir_tabelas(con)
    return con


def gerar_slug(nome):
    """Endereço amigável: sem acento, minúsculo, hífens (ex.: 'Jurídico 2026' -> 'juridico-2026')."""
    texto = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode("ascii").lower()
    texto = re.sub(r"[^a-z0-9]+", "-", texto).strip("-")[:50].strip("-")
    return texto or "projeto"


def slug_valido(slug):
    return bool(slug and _SLUG_VALIDO.match(slug))


def _validar(nome, descricao):
    nome = (nome or "").strip()
    descricao = (descricao or "").strip()
    if not nome:
        raise ValueError("Informe o nome do projeto.")
    if len(nome) > LIMITE_NOME:
        raise ValueError(f"O nome pode ter até {LIMITE_NOME} caracteres.")
    if len(descricao) > LIMITE_DESCRICAO:
        raise ValueError(f"A descrição pode ter até {LIMITE_DESCRICAO} caracteres.")
    return nome, descricao or None


def _indices_validos(lista):
    """Só índices gerenciados, sem repetição, na ordem recebida."""
    vistos = []
    for nome in lista or []:
        if indices.eh_gerenciado(nome) and nome not in vistos:
            vistos.append(nome)
    return vistos


def listar(usuario_id=None):
    """Projetos com a lista dos índices e a quantidade de usuários de cada um.
    Com `usuario_id`, só os projetos a que essa pessoa tem acesso."""
    with _conectar() as con, con.cursor() as cur:
        if usuario_id is None:
            cur.execute("SELECT id, slug, nome, descricao FROM projetos ORDER BY nome")
        else:
            cur.execute(
                "SELECT id, slug, nome, descricao FROM projetos WHERE id IN "
                "(SELECT projeto_id FROM projeto_usuarios WHERE usuario_id = %s) ORDER BY nome",
                (usuario_id,),
            )
        projetos = cur.fetchall()
        cur.execute("SELECT projeto_id, indice FROM projeto_indices ORDER BY indice")
        por_projeto = {}
        for linha in cur.fetchall():
            por_projeto.setdefault(linha["projeto_id"], []).append(linha["indice"])
        cur.execute("SELECT projeto_id, COUNT(*) AS n FROM projeto_usuarios GROUP BY projeto_id")
        usuarios_por_projeto = {linha["projeto_id"]: linha["n"] for linha in cur.fetchall()}
    for p in projetos:
        p["indices"] = por_projeto.get(p["id"], [])
        p["qtd_usuarios"] = usuarios_por_projeto.get(p["id"], 0)
    return projetos


def _obter(where, valor):
    with _conectar() as con, con.cursor() as cur:
        cur.execute(f"SELECT id, slug, nome, descricao FROM projetos WHERE {where} = %s", (valor,))
        projeto = cur.fetchone()
        if not projeto:
            return None
        cur.execute("SELECT indice FROM projeto_indices WHERE projeto_id = %s ORDER BY indice", (projeto["id"],))
        projeto["indices"] = [linha["indice"] for linha in cur.fetchall()]
        cur.execute("SELECT usuario_id FROM projeto_usuarios WHERE projeto_id = %s", (projeto["id"],))
        projeto["usuarios"] = [linha["usuario_id"] for linha in cur.fetchall()]
    return projeto


def obter_por_slug(slug):
    return _obter("slug", slug) if slug_valido(slug) else None


def obter(projeto_id):
    return _obter("id", projeto_id)


def criar(nome, descricao, lista_indices, usuarios=None):
    """Cria o projeto e devolve o registro. O endereço (slug) vem do nome e,
    se já existir, ganha um sufixo (-2, -3...). Levanta ValueError."""
    nome, descricao = _validar(nome, descricao)
    base = gerar_slug(nome)
    with _conectar() as con, con.cursor() as cur:
        slug, n = base, 1
        while True:
            cur.execute("SELECT 1 FROM projetos WHERE slug = %s", (slug,))
            if not cur.fetchone():
                break
            n += 1
            slug = f"{base[:46]}-{n}"
        cur.execute(
            "INSERT INTO projetos (slug, nome, descricao, criado_em) VALUES (%s, %s, %s, UTC_TIMESTAMP())",
            (slug, nome, descricao),
        )
        projeto_id = cur.lastrowid
        _gravar_indices(cur, projeto_id, _indices_validos(lista_indices))
        _gravar_usuarios(cur, projeto_id, usuarios or [])
    return obter(projeto_id)


def atualizar(projeto_id, nome, descricao, lista_indices, usuarios=None):
    """Atualiza nome, descrição, índices e (se `usuarios` vier) quem acessa.
    O endereço não muda, para os links salvos continuarem valendo. Levanta ValueError."""
    nome, descricao = _validar(nome, descricao)
    with _conectar() as con, con.cursor() as cur:
        cur.execute("UPDATE projetos SET nome = %s, descricao = %s WHERE id = %s", (nome, descricao, projeto_id))
        cur.execute("DELETE FROM projeto_indices WHERE projeto_id = %s", (projeto_id,))
        _gravar_indices(cur, projeto_id, _indices_validos(lista_indices))
        if usuarios is not None:
            cur.execute("DELETE FROM projeto_usuarios WHERE projeto_id = %s", (projeto_id,))
            _gravar_usuarios(cur, projeto_id, usuarios)


def _gravar_indices(cur, projeto_id, lista):
    for indice in lista:
        cur.execute("INSERT INTO projeto_indices (projeto_id, indice) VALUES (%s, %s)", (projeto_id, indice))


def _gravar_usuarios(cur, projeto_id, usuario_ids):
    for usuario_id in sorted({int(u) for u in usuario_ids}):
        cur.execute("INSERT INTO projeto_usuarios (projeto_id, usuario_id) VALUES (%s, %s)", (projeto_id, usuario_id))


# ---- Acesso dos usuários aos projetos ---------------------------------------
# Administradores veem todos os projetos e índices; os demais só os projetos
# em que foram incluídos (aqui ou na tela do usuário) e os índices deles.

def ids_do_usuario(usuario_id):
    """Ids dos projetos a que o usuário tem acesso."""
    with _conectar() as con, con.cursor() as cur:
        cur.execute("SELECT projeto_id FROM projeto_usuarios WHERE usuario_id = %s", (usuario_id,))
        return {linha["projeto_id"] for linha in cur.fetchall()}


def indices_do_usuario(usuario_id):
    """Índices que o usuário pode pesquisar: os de todos os projetos dele."""
    with _conectar() as con, con.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT pi.indice FROM projeto_indices pi "
            "JOIN projeto_usuarios pu ON pu.projeto_id = pi.projeto_id WHERE pu.usuario_id = %s",
            (usuario_id,),
        )
        return {linha["indice"] for linha in cur.fetchall() if indices.eh_gerenciado(linha["indice"])}


def definir_projetos_do_usuario(usuario_id, projeto_ids):
    """Troca os projetos do usuário pela lista dada (tela do usuário)."""
    with _conectar() as con, con.cursor() as cur:
        cur.execute("DELETE FROM projeto_usuarios WHERE usuario_id = %s", (usuario_id,))
        validos = set()
        if projeto_ids:
            cur.execute("SELECT id FROM projetos")
            existentes = {linha["id"] for linha in cur.fetchall()}
            validos = {int(p) for p in projeto_ids} & existentes
        for projeto_id in sorted(validos):
            cur.execute("INSERT INTO projeto_usuarios (projeto_id, usuario_id) VALUES (%s, %s)", (projeto_id, usuario_id))


def projetos_por_usuario():
    """{usuario_id: quantidade de projetos}, para a lista de usuários."""
    with _conectar() as con, con.cursor() as cur:
        cur.execute("SELECT usuario_id, COUNT(*) AS n FROM projeto_usuarios GROUP BY usuario_id")
        return {linha["usuario_id"]: linha["n"] for linha in cur.fetchall()}


def apagar(projeto_id):
    """Remove só o projeto e seus vínculos; índices e documentos não são tocados."""
    with _conectar() as con, con.cursor() as cur:
        cur.execute("DELETE FROM projetos WHERE id = %s", (projeto_id,))
        return cur.rowcount == 1


def desvincular_indice(indice):
    """Chamado quando um índice é apagado: tira ele de todos os projetos."""
    with _conectar() as con, con.cursor() as cur:
        cur.execute("DELETE FROM projeto_indices WHERE indice = %s", (indice,))
        return cur.rowcount
