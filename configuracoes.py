"""Configurações editáveis pela administração (tabela `configuracoes` no MySQL,
chave → valor, criada sozinha no primeiro uso).

Guarda o que muda sem redeploy: servidor SMTP, remetente, endereço do site
(para os links dos e-mails) e as opções dos alertas do sistema. **Senhas não
ficam aqui**: a do SMTP vem só da variável de ambiente `SMTP_SENHA`,
cadastrada no Dokploy, para não ficar em texto puro no banco nem aparecer na tela.
"""

import threading

from db import conectar

_DDL = """
CREATE TABLE IF NOT EXISTS configuracoes (
    chave         VARCHAR(100) PRIMARY KEY,
    valor         TEXT         NULL,
    atualizado_em DATETIME     NOT NULL
) CHARACTER SET utf8mb4
"""

PADROES = {
    "smtp_host": "",
    "smtp_porta": "587",
    "smtp_seguranca": "starttls",   # starttls | ssl | nenhuma
    "smtp_usuario": "",
    "smtp_remetente": "",
    "url_base": "",
    "alertas_ativos": "0",
    "alertas_destinatarios": "",
    "alertas_fontes": "1",
    "alertas_fila": "1",
    "alertas_indices": "1",
}

_tabela_ok = False
_trava = threading.Lock()


def _conectar():
    global _tabela_ok
    con = conectar()
    with _trava:
        if not _tabela_ok:
            with con.cursor() as cur:
                cur.execute(_DDL)
            _tabela_ok = True
    return con


def ler_todas():
    """Todas as configurações, com os valores padrão para as que nunca foram salvas."""
    with _conectar() as con, con.cursor() as cur:
        cur.execute("SELECT chave, valor FROM configuracoes")
        salvas = {linha["chave"]: linha["valor"] for linha in cur.fetchall()}
    return {chave: salvas[chave] if salvas.get(chave) is not None else padrao for chave, padrao in PADROES.items()}


def salvar(valores):
    """Grava só as chaves conhecidas."""
    linhas = [(chave, str(valor)) for chave, valor in valores.items() if chave in PADROES]
    if not linhas:
        return
    with _conectar() as con, con.cursor() as cur:
        cur.executemany(
            "INSERT INTO configuracoes (chave, valor, atualizado_em) VALUES (%s, %s, UTC_TIMESTAMP())"
            " ON DUPLICATE KEY UPDATE valor = VALUES(valor), atualizado_em = VALUES(atualizado_em)",
            linhas,
        )
