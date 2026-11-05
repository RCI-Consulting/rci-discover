"""Usuários locais: login com e-mail e senha, guardados no MySQL.

Só administradores cadastram usuários (Administração → Usuários). Cada
usuário tem um papel: `admin` (vê a administração) ou `usuario` (busca,
projetos, pastas e buscas salvas). Quem recebe uma senha criada pelo admin
precisa trocá-la no primeiro acesso.

O primeiro administrador vem das variáveis de ambiente ADMIN_EMAIL e
ADMIN_SENHA: enquanto a tabela estiver vazia, ele é criado no primeiro acesso
à tela de login (e também precisa trocar a senha ao entrar).

As senhas nunca são guardadas: só o hash (`werkzeug.security`, scrypt).
Tabela `usuarios`, criada sozinha no primeiro uso, como as outras.
"""

import re
import secrets
import threading
import time

import pymysql
from werkzeug.security import check_password_hash, generate_password_hash

import config
from db import conectar

_DDL = """
CREATE TABLE IF NOT EXISTS usuarios (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    email         VARCHAR(255) NOT NULL UNIQUE,
    nome          VARCHAR(100) NOT NULL,
    senha_hash    VARCHAR(255) NOT NULL,
    papel         VARCHAR(20)  NOT NULL DEFAULT 'usuario',
    ativo         TINYINT(1)   NOT NULL DEFAULT 1,
    trocar_senha  TINYINT(1)   NOT NULL DEFAULT 1,
    criado_em     DATETIME     NOT NULL,
    atualizado_em DATETIME     NOT NULL,
    ultimo_login  DATETIME     NULL
) CHARACTER SET utf8mb4
"""

PAPEIS = {"admin": "Administrador", "usuario": "Usuário"}
SENHA_MINIMA = 10
LIMITE_NOME = 100
_EMAIL_VALIDO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_COLUNAS = "SELECT id, email, nome, papel, ativo, trocar_senha, criado_em, ultimo_login FROM usuarios"

# Hash de uma senha qualquer: quando o e-mail não existe, conferimos contra ele
# mesmo assim, para a resposta levar o mesmo tempo e não revelar quem existe.
_HASH_FALSO = generate_password_hash(secrets.token_hex(16))

_tabela_ok = False
_trava = threading.Lock()


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


# ---- Validação -------------------------------------------------------------

def normalizar_email(email):
    return (email or "").strip().lower()


def validar_email(email):
    email = normalizar_email(email)
    if not _EMAIL_VALIDO.match(email) or len(email) > 255:
        raise ValueError("Informe um e-mail válido.")
    return email


def validar_nome(nome):
    nome = re.sub(r"\s+", " ", nome or "").strip()
    if not nome:
        raise ValueError("Informe o nome.")
    if len(nome) > LIMITE_NOME:
        raise ValueError(f"O nome pode ter até {LIMITE_NOME} caracteres.")
    return nome


def validar_senha(senha, confirmacao=None):
    senha = senha or ""
    if len(senha) < SENHA_MINIMA:
        raise ValueError(f"A senha precisa ter pelo menos {SENHA_MINIMA} caracteres.")
    if len(senha) > 200:
        raise ValueError("A senha pode ter até 200 caracteres.")
    if not (re.search(r"[A-Za-z]", senha) and re.search(r"\d", senha)):
        raise ValueError("A senha precisa ter letras e números.")
    if confirmacao is not None and senha != confirmacao:
        raise ValueError("A confirmação não confere com a senha.")
    return senha


def validar_papel(papel):
    if papel not in PAPEIS:
        raise ValueError("Escolha um papel válido.")
    return papel


def senha_temporaria():
    """Senha aleatória para o admin repassar; o usuário troca no 1º acesso."""
    return secrets.token_urlsafe(9) + str(secrets.randbelow(10))


# ---- Consultas -------------------------------------------------------------

def contar():
    with _conectar() as con, con.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM usuarios")
        return cur.fetchone()["n"]


def listar():
    with _conectar() as con, con.cursor() as cur:
        cur.execute(f"{_COLUNAS} ORDER BY ativo DESC, nome")
        return cur.fetchall()


def obter(usuario_id):
    with _conectar() as con, con.cursor() as cur:
        cur.execute(f"{_COLUNAS} WHERE id = %s", (usuario_id,))
        return cur.fetchone()


def _admins_ativos(cur, exceto=None):
    cur.execute("SELECT COUNT(*) AS n FROM usuarios WHERE papel = 'admin' AND ativo = 1 AND id <> %s", (exceto or 0,))
    return cur.fetchone()["n"]


# ---- Escrita ---------------------------------------------------------------

def criar(email, nome, papel, senha, trocar_senha=True):
    """Cria o usuário e devolve o id. Levanta ValueError com a mensagem pronta."""
    email, nome, papel = validar_email(email), validar_nome(nome), validar_papel(papel)
    validar_senha(senha)
    try:
        with _conectar() as con, con.cursor() as cur:
            cur.execute(
                "INSERT INTO usuarios (email, nome, senha_hash, papel, ativo, trocar_senha, criado_em, atualizado_em) "
                "VALUES (%s, %s, %s, %s, 1, %s, UTC_TIMESTAMP(), UTC_TIMESTAMP())",
                (email, nome, generate_password_hash(senha), papel, 1 if trocar_senha else 0),
            )
            return cur.lastrowid
    except pymysql.err.IntegrityError:
        raise ValueError("Já existe um usuário com esse e-mail.")


def atualizar(usuario_id, nome, papel, ativo):
    """Muda nome, papel e situação. Nunca deixa o sistema sem um admin ativo."""
    nome, papel = validar_nome(nome), validar_papel(papel)
    with _conectar() as con, con.cursor() as cur:
        if (papel != "admin" or not ativo) and _admins_ativos(cur, exceto=usuario_id) == 0:
            raise ValueError("Esse é o último administrador ativo: mantenha-o administrador e ativo.")
        cur.execute(
            "UPDATE usuarios SET nome = %s, papel = %s, ativo = %s, atualizado_em = UTC_TIMESTAMP() WHERE id = %s",
            (nome, papel, 1 if ativo else 0, usuario_id),
        )


def definir_senha(usuario_id, senha, trocar_senha):
    validar_senha(senha)
    with _conectar() as con, con.cursor() as cur:
        cur.execute(
            "UPDATE usuarios SET senha_hash = %s, trocar_senha = %s, atualizado_em = UTC_TIMESTAMP() WHERE id = %s",
            (generate_password_hash(senha), 1 if trocar_senha else 0, usuario_id),
        )


def conferir_senha(usuario_id, senha):
    with _conectar() as con, con.cursor() as cur:
        cur.execute("SELECT senha_hash FROM usuarios WHERE id = %s", (usuario_id,))
        linha = cur.fetchone()
    return bool(linha) and check_password_hash(linha["senha_hash"], senha or "")


def autenticar(email, senha):
    """O usuário (sem o hash) se e-mail e senha conferem e ele está ativo; senão None."""
    email = normalizar_email(email)
    with _conectar() as con, con.cursor() as cur:
        cur.execute(f"{_COLUNAS.replace('SELECT ', 'SELECT senha_hash, ', 1)} WHERE email = %s", (email,))
        linha = cur.fetchone()
        valida = check_password_hash(linha["senha_hash"] if linha else _HASH_FALSO, senha or "")
        if not (linha and valida and linha["ativo"]):
            return None
        cur.execute("UPDATE usuarios SET ultimo_login = UTC_TIMESTAMP() WHERE id = %s", (linha["id"],))
    linha.pop("senha_hash", None)
    return linha


def garantir_admin_inicial():
    """Com a tabela vazia e ADMIN_EMAIL/ADMIN_SENHA definidos, cria o 1º admin.
    Devolve True se criou."""
    if not (config.ADMIN_EMAIL and config.ADMIN_SENHA):
        return False
    if contar() > 0:
        return False
    try:
        criar(config.ADMIN_EMAIL, config.ADMIN_NOME or "Administrador", "admin", config.ADMIN_SENHA, trocar_senha=True)
    except ValueError as e:
        print(f"[Usuários] Não consegui criar o administrador inicial: {e}")
        return False
    print(f"[Usuários] Administrador inicial criado: {normalizar_email(config.ADMIN_EMAIL)}")
    return True


# ---- Tentativas de login ---------------------------------------------------
# Em memória, por processo: depois de MAX_FALHAS senhas erradas para o mesmo
# e-mail ou do mesmo IP em JANELA segundos, o login fica bloqueado até a janela
# passar. Simples, mas corta tentativa de adivinhar senha.

MAX_FALHAS = 5
JANELA = 15 * 60
_falhas = {}
_trava_falhas = threading.Lock()


def _chaves(email, ip):
    return [("email", normalizar_email(email)), ("ip", ip or "")]


def bloqueado(email, ip):
    agora = time.monotonic()
    with _trava_falhas:
        for chave in _chaves(email, ip):
            recentes = [t for t in _falhas.get(chave, []) if agora - t < JANELA]
            _falhas[chave] = recentes
            if len(recentes) >= MAX_FALHAS:
                return True
    return False


def registrar_falha(email, ip):
    agora = time.monotonic()
    with _trava_falhas:
        for chave in _chaves(email, ip):
            _falhas.setdefault(chave, []).append(agora)


def limpar_falhas(email, ip):
    with _trava_falhas:
        for chave in _chaves(email, ip):
            _falhas.pop(chave, None)
