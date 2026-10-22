"""Auditoria das buscas: guarda no MySQL o que foi pesquisado, quantos
resultados voltaram, quando, e (quando houver login / proxy) quem e de onde.

A gravação roda em segundo plano e nunca propaga erro: se o banco estiver
fora ou lento, a busca do usuário continua normal e a falha só vai pro log.
"""

import re
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
    filtros          VARCHAR(500)  NULL,
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
            # Tabela criada antes das facetas não tem a coluna `filtros`.
            cur.execute("SHOW COLUMNS FROM auditoria_buscas LIKE 'filtros'")
            if not cur.fetchone():
                cur.execute("ALTER TABLE auditoria_buscas ADD COLUMN filtros VARCHAR(500) NULL")
        _tabela_ok = True


def _gravar(termo, total, usuario, ip, filtros):
    try:
        with conectar() as con:
            _garantir_tabela(con)
            with con.cursor() as cur:
                cur.execute(
                    "INSERT INTO auditoria_buscas (criado_em, termo, total_resultados, usuario, ip, filtros) "
                    "VALUES (UTC_TIMESTAMP(), %s, %s, %s, %s, %s)",
                    (termo[:1000], int(total), usuario[:255] if usuario else None, ip[:45] if ip else None,
                     filtros[:500] if filtros else None),
                )
    except Exception as e:
        print(f"[Auditoria] Não consegui registrar a busca: {e}")


def registrar_busca(termo, total, usuario=None, ip=None, filtros=None):
    """Dispara a gravação e volta na hora. `filtros` é o texto das facetas
    ativas (ex.: "tipo=pdf; data=7d"), ou None."""
    threading.Thread(target=_gravar, args=(termo, total, usuario, ip, filtros), daemon=True).start()


def contexto_requisicao():
    """(usuario, ip) da requisição atual.

    O IP vem de `request.remote_addr`, que o ProxyFix (web_app.py) ajusta
    para o IP real atrás do Traefik. O usuário lê `session["usuario"]`, a
    chave que o login com Microsoft (PR #3) grava; sem login fica None."""
    logado = session.get("usuario")
    if isinstance(logado, dict):
        logado = logado.get("email") or logado.get("nome")
    return (logado or None), request.remote_addr


# ---- Consulta (tela Administração > Auditoria) ----------------------------

def _filtro(dias, termo):
    """WHERE e parâmetros: período em dias (0 = tudo) e termo contendo o texto."""
    condicoes, params = [], []
    if dias:
        condicoes.append("criado_em >= UTC_TIMESTAMP() - INTERVAL %s DAY")
        params.append(int(dias))
    if termo:
        escapado = termo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        condicoes.append("termo LIKE %s")
        params.append(f"%{escapado}%")
    return ("WHERE " + " AND ".join(condicoes)) if condicoes else "", params


def resumo(dias, termo):
    """Totais do período: buscas, buscas sem resultado e termos distintos."""
    where, params = _filtro(dias, termo)
    with conectar() as con:
        _garantir_tabela(con)
        with con.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS buscas, "
                "COALESCE(SUM(total_resultados = 0), 0) AS sem_resultado, "
                f"COUNT(DISTINCT termo) AS termos FROM auditoria_buscas {where}",
                params,
            )
            return cur.fetchone()


def mais_buscados(dias, termo, sem_resultado=False, limite=10):
    """Termos mais pesquisados (ou, com sem_resultado, os que não acharam nada)."""
    where, params = _filtro(dias, termo)
    if sem_resultado:
        where += (" AND " if where else "WHERE ") + "total_resultados = 0"
    with conectar() as con:
        _garantir_tabela(con)
        with con.cursor() as cur:
            cur.execute(
                f"SELECT termo, COUNT(*) AS vezes FROM auditoria_buscas {where} "
                "GROUP BY termo ORDER BY vezes DESC, MAX(id) DESC LIMIT %s",
                params + [int(limite)],
            )
            return cur.fetchall()


def listar(dias, termo, pagina=1, por_pagina=50):
    """Buscas mais recentes primeiro. Retorna (linhas, total de linhas)."""
    where, params = _filtro(dias, termo)
    pagina = max(1, int(pagina))
    with conectar() as con:
        _garantir_tabela(con)
        with con.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM auditoria_buscas {where}", params)
            total = cur.fetchone()["n"]
            cur.execute(
                "SELECT criado_em, termo, total_resultados, usuario, ip, filtros "
                f"FROM auditoria_buscas {where} ORDER BY id DESC LIMIT %s OFFSET %s",
                params + [int(por_pagina), (pagina - 1) * int(por_pagina)],
            )
            return cur.fetchall(), total


LIMITE_EXPORTACAO = 100000


def exportar(dias, termo, limite=LIMITE_EXPORTACAO):
    """Todas as buscas do filtro (mais recentes primeiro), para o CSV.
    Retorna (linhas, se cortou no limite)."""
    where, params = _filtro(dias, termo)
    with conectar() as con:
        _garantir_tabela(con)
        with con.cursor() as cur:
            cur.execute(
                "SELECT criado_em, termo, total_resultados, filtros, usuario, ip "
                f"FROM auditoria_buscas {where} ORDER BY id DESC LIMIT %s",
                params + [int(limite) + 1],
            )
            linhas = cur.fetchall()
    return linhas[:limite], len(linhas) > limite


# ---- Sugestões (autocomplete e "buscas populares") -------------------------

def _termo_sensivel(termo):
    """E-mail ou sequência longa de dígitos (CPF, CNPJ, telefone): nunca vira
    sugestão para outras pessoas, mesmo que tenha sido muito buscado."""
    return "@" in termo or bool(re.search(r"\d{7,}|(?:\d[\s.\-/]*){9,}", termo))


def termos_populares(prefixo=None, slug=None, minimo=2, limite=5):
    """Buscas anteriores que acharam algo, da mais repetida para a menos. Só
    entram termos buscados pelo menos `minimo` vezes (um termo digitado uma vez
    por uma pessoa não é exposto às outras). `slug` limita às buscas feitas
    naquele projeto; `prefixo` ao que começa com o texto."""
    condicoes = ["total_resultados > 0", "criado_em >= UTC_TIMESTAMP() - INTERVAL 180 DAY"]
    params = []
    if prefixo:
        escapado = prefixo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        condicoes.append("termo LIKE %s")
        params.append(f"{escapado}%")
    if slug:
        condicoes.append("(filtros = %s OR filtros LIKE %s)")
        params += [f"projeto={slug}", f"projeto={slug};%"]
    with conectar() as con:
        _garantir_tabela(con)
        with con.cursor() as cur:
            cur.execute(
                f"SELECT termo, COUNT(*) AS vezes FROM auditoria_buscas WHERE {' AND '.join(condicoes)} "
                "GROUP BY termo HAVING COUNT(*) >= %s ORDER BY vezes DESC, MAX(id) DESC LIMIT %s",
                params + [int(minimo), int(limite) * 3],
            )
            linhas = cur.fetchall()
    return [l["termo"] for l in linhas if not _termo_sensivel(l["termo"])][: int(limite)]
