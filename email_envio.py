"""Envio de e-mails pelo SMTP configurado na administração (relatórios,
alertas do sistema e alertas de buscas salvas).

Cada envio fica registrado na tabela `emails_enviados` (para quem, assunto,
se deu certo e o erro), mostrada na tela de e-mail da administração.
"""

import os
import re
import smtplib
import ssl
import threading
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import configuracoes
from db import conectar

_DDL = """
CREATE TABLE IF NOT EXISTS emails_enviados (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    enviado_em    DATETIME      NOT NULL,
    origem        VARCHAR(50)   NOT NULL,
    destinatarios VARCHAR(2000) NOT NULL,
    assunto       VARCHAR(300)  NOT NULL,
    ok            TINYINT(1)    NOT NULL,
    erro          TEXT          NULL,
    INDEX idx_enviado (enviado_em)
) CHARACTER SET utf8mb4
"""

_EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
MAX_DESTINATARIOS = 50
TIMEOUT = 20

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


def senha_definida():
    return bool(os.getenv("SMTP_SENHA"))


def separar_emails(texto):
    """'a@x.com; b@y.com, a@x.com' -> ['a@x.com', 'b@y.com']. Levanta
    ValueError com o primeiro endereço inválido."""
    vistos, lista = set(), []
    for parte in re.split(r"[,;\s]+", texto or ""):
        parte = parte.strip()
        if not parte:
            continue
        if not _EMAIL.match(parte):
            raise ValueError(f"E-mail inválido: {parte}")
        if parte.lower() not in vistos:
            vistos.add(parte.lower())
            lista.append(parte)
    if len(lista) > MAX_DESTINATARIOS:
        raise ValueError(f"Use no máximo {MAX_DESTINATARIOS} destinatários.")
    return lista


def configurado(conf=None):
    conf = conf or configuracoes.ler_todas()
    return bool(conf["smtp_host"] and conf["smtp_remetente"])


def _registrar(origem, destinatarios, assunto, ok, erro):
    try:
        with _conectar() as con, con.cursor() as cur:
            cur.execute(
                "INSERT INTO emails_enviados (enviado_em, origem, destinatarios, assunto, ok, erro)"
                " VALUES (UTC_TIMESTAMP(), %s, %s, %s, %s, %s)",
                (origem[:50], ", ".join(destinatarios)[:2000], assunto[:300], 1 if ok else 0, (erro or "")[:2000] or None),
            )
    except Exception as e:
        print(f"[E-mail] Não consegui registrar o envio: {e}")


def enviar(destinatarios, assunto, html, origem, anexos=(), conf=None):
    """Envia um e-mail HTML (com versão em texto simples) para cada
    destinatário em cópia oculta. `anexos`: [(nome, bytes, tipo MIME)].
    Levanta RuntimeError com uma mensagem legível se falhar."""
    conf = conf or configuracoes.ler_todas()
    if not configurado(conf):
        raise RuntimeError("O servidor de e-mail ainda não foi configurado (Administração → E-mail).")
    if not destinatarios:
        raise RuntimeError("Nenhum destinatário.")

    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = formataddr(("RCISearch", conf["smtp_remetente"]))
    msg["To"] = formataddr(("RCISearch", conf["smtp_remetente"]))  # destinatários vão em cópia oculta
    msg["Message-ID"] = make_msgid(domain=conf["smtp_remetente"].split("@")[-1])
    msg.set_content("Este e-mail precisa de um leitor com suporte a HTML.\n")
    msg.add_alternative(html, subtype="html")
    for nome, dados, tipo in anexos:
        principal, _, sub = tipo.partition("/")
        msg.add_attachment(dados, maintype=principal, subtype=sub or "octet-stream", filename=nome)

    try:
        porta = int(conf["smtp_porta"] or 0) or (465 if conf["smtp_seguranca"] == "ssl" else 587)
        contexto = ssl.create_default_context()
        if conf["smtp_seguranca"] == "ssl":
            servidor = smtplib.SMTP_SSL(conf["smtp_host"], porta, timeout=TIMEOUT, context=contexto)
        else:
            servidor = smtplib.SMTP(conf["smtp_host"], porta, timeout=TIMEOUT)
        with servidor:
            if conf["smtp_seguranca"] == "starttls":
                servidor.starttls(context=contexto)
            if conf["smtp_usuario"]:
                servidor.login(conf["smtp_usuario"], os.getenv("SMTP_SENHA", ""))
            servidor.send_message(msg, to_addrs=list(destinatarios))
    except Exception as e:
        erro = f"{type(e).__name__}: {e}"
        _registrar(origem, destinatarios, assunto, False, erro)
        raise RuntimeError(f"Falha ao enviar o e-mail: {erro}")
    _registrar(origem, destinatarios, assunto, True, None)


def ultimos_envios(limite=20):
    with _conectar() as con, con.cursor() as cur:
        cur.execute("SELECT * FROM emails_enviados ORDER BY id DESC LIMIT %s", (limite,))
        return cur.fetchall()
