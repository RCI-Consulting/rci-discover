"""Auditoria das buscas: guarda no MySQL o que foi pesquisado, quantos
resultados voltaram, quando, e (quando houver login / proxy) quem e de onde.

A gravação roda em segundo plano e nunca propaga erro: se o banco estiver
fora ou lento, a busca do usuário continua normal e a falha só vai pro log.
"""

import threading

from flask import request, session

from db import conectar

_DDL = """
CREATE TABLE IF NOT EXISTS auditoria_buscas (
    id               BIGINT AUTO_INCREMENT PRIMARY KEY,
    criado_em        DATETIME      NOT NULL,
    termo            VARCHAR(1000) NOT NULL,
    total_resultados INT           NOT NULL,
    usuario          VARCHAR(255)  NULL,
    ip               VARCHAR(45)   NULL,
    INDEX idx_criado_em (criado_em),
    INDEX idx_usuario (usuario)
) CHARACTER SET utf8mb4
"""

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


def _gravar(termo, total, usuario, ip):
    try:
        with conectar() as con:
            _garantir_tabela(con)
            with con.cursor() as cur:
                cur.execute(
                    "INSERT INTO auditoria_buscas (criado_em, termo, total_resultados, usuario, ip) "
                    "VALUES (UTC_TIMESTAMP(), %s, %s, %s, %s)",
                    (termo[:1000], int(total), usuario[:255] if usuario else None, ip[:45] if ip else None),
                )
    except Exception as e:
        print(f"[Auditoria] Não consegui registrar a busca: {e}")


def registrar_busca(termo, total, usuario=None, ip=None):
    """Dispara a gravação e volta na hora."""
    threading.Thread(target=_gravar, args=(termo, total, usuario, ip), daemon=True).start()


def contexto_requisicao():
    """(usuario, ip) da requisição atual.

    O IP vem de `request.remote_addr`, que o ProxyFix (web_app.py) ajusta
    para o IP real atrás do Traefik. O usuário lê `session["usuario"]`, a
    chave que o login com Microsoft (PR #3) grava; sem login fica None."""
    logado = session.get("usuario")
    if isinstance(logado, dict):
        logado = logado.get("email") or logado.get("nome")
    return (logado or None), request.remote_addr
