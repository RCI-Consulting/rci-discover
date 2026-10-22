"""Relatórios da auditoria: o CSV (download na tela e anexo dos e-mails) e o
resumo em HTML dos relatórios agendados.

O CSV usa `;` e UTF-8 com BOM, o que o Excel em português abre direto, com
acentos e colunas certas. Termos que começam com `=`, `+`, `-` ou `@`
ganham um apóstrofo na frente: sem isso o Excel os executaria como fórmula
(alguém poderia buscar `=HYPERLINK(...)` de propósito).
"""

import csv
import io
import threading
from datetime import datetime, timedelta, timezone
from html import escape

import auditoria
import config
import configuracoes
import email_envio
from db import conectar

_INICIO_PERIGOSO = ("=", "+", "-", "@", "\t", "\r")


def _celula(valor):
    texto = "" if valor is None else str(valor)
    return "'" + texto if texto.startswith(_INICIO_PERIGOSO) else texto


def csv_auditoria(linhas):
    """Bytes do CSV com as linhas de `auditoria.exportar`."""
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";", lineterminator="\r\n")
    escritor.writerow(["Data e hora (Brasília)", "Termo", "Resultados", "Filtros", "Usuário", "IP"])
    for l in linhas:
        quando = l["criado_em"] + timedelta(hours=config.FUSO_HORAS)
        escritor.writerow([
            quando.strftime("%d/%m/%Y %H:%M:%S"), _celula(l["termo"]), l["total_resultados"],
            _celula(l["filtros"]), _celula(l["usuario"]), _celula(l["ip"]),
        ])
    return saida.getvalue().encode("utf-8-sig")


def nome_periodo(dias):
    return {1: "últimas 24 horas", 7: "últimos 7 dias", 30: "últimos 30 dias", 90: "últimos 90 dias"}.get(
        dias, f"últimos {dias} dias" if dias else "todo o período")


def montar_relatorio_auditoria(dias, url_base=""):
    """(assunto, html, anexos) do relatório agendado da auditoria."""
    res = auditoria.resumo(dias, "")
    populares = auditoria.mais_buscados(dias, "", limite=10)
    sem = auditoria.mais_buscados(dias, "", sem_resultado=True, limite=10)
    linhas, cortou = auditoria.exportar(dias, "")
    periodo = nome_periodo(dias)

    def tabela(titulo, itens, vazio):
        if not itens:
            return f"<h3>{titulo}</h3><p style='color:#5f6b7a'>{vazio}</p>"
        corpo = "".join(
            f"<tr><td style='padding:3px 12px 3px 0'>{escape(i['termo'])}</td>"
            f"<td style='text-align:right;color:#5f6b7a'>{i['vezes']}×</td></tr>" for i in itens
        )
        return f"<h3>{titulo}</h3><table style='border-collapse:collapse;font-size:14px'>{corpo}</table>"

    link = (f"<p><a href='{escape(url_base.rstrip('/'))}/admin/auditoria?dias={dias}'>Abrir a auditoria no RCISearch</a></p>"
            if url_base else "")
    aviso = (f"<p style='color:#8a1f1f'>O CSV anexo traz só as {len(linhas)} buscas mais recentes.</p>" if cortou else "")
    html = f"""
<div style="font-family:Arial,Helvetica,sans-serif;color:#1a2333;max-width:640px">
  <h2 style="color:#03143b;margin-bottom:4px">Relatório de buscas — {periodo}</h2>
  <p style="font-size:15px">
    <strong>{res['buscas']}</strong> buscas ·
    <strong>{res['termos']}</strong> termos diferentes ·
    <strong>{res['sem_resultado']}</strong> sem resultado
  </p>
  {tabela("Mais buscados", populares, "Nenhuma busca no período.")}
  {tabela("Sem resultado (o que falta indexar)", sem, "Toda busca do período achou algo.")}
  <p style="color:#5f6b7a;font-size:13px">O histórico completo do período está no CSV anexo.</p>
  {aviso}{link}
</div>"""
    anexo = ("auditoria-buscas.csv", csv_auditoria(linhas), "text/csv")
    return f"RCISearch — relatório de buscas ({periodo})", html, [anexo]


# ---- Relatórios agendados (enviados por e-mail pelo notificacoes.py) --------

_DDL = """
CREATE TABLE IF NOT EXISTS relatorios_agendados (
    id             BIGINT AUTO_INCREMENT PRIMARY KEY,
    nome           VARCHAR(100)  NOT NULL,
    tipo           VARCHAR(30)   NOT NULL,
    periodo_dias   INT           NOT NULL,
    frequencia     VARCHAR(20)   NOT NULL,
    hora           INT           NOT NULL,
    destinatarios  VARCHAR(2000) NOT NULL,
    ativo          TINYINT(1)    NOT NULL DEFAULT 1,
    proxima_em     DATETIME      NULL,
    ultimo_envio   DATETIME      NULL,
    ultimo_status  VARCHAR(500)  NULL,
    criado_em      DATETIME      NOT NULL
) CHARACTER SET utf8mb4
"""

TIPOS = {"auditoria": "Auditoria das buscas"}
FREQUENCIAS = [("diaria", "Todo dia"), ("semanal", "Toda segunda-feira"), ("mensal", "Todo dia 1º do mês")]
PERIODOS = [(1, "Últimas 24 horas"), (7, "Últimos 7 dias"), (30, "Últimos 30 dias"), (90, "Últimos 90 dias")]
LIMITE_NOME = 100

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


def proxima_execucao(frequencia, hora, agora=None):
    """Próximo horário (UTC, sem fuso no objeto, como o MySQL guarda) em que o
    relatório deve sair: `hora` é a hora cheia no horário de Brasília."""
    agora = agora or datetime.now(timezone.utc).replace(tzinfo=None)
    fuso = timedelta(hours=config.FUSO_HORAS)
    local = agora + fuso
    alvo = local.replace(hour=hora, minute=0, second=0, microsecond=0)
    if frequencia == "semanal":
        alvo += timedelta(days=(7 - alvo.weekday()) % 7)  # segunda-feira
        if alvo <= local:
            alvo += timedelta(days=7)
    elif frequencia == "mensal":
        alvo = alvo.replace(day=1)
        if alvo <= local:
            alvo = (alvo.replace(day=28) + timedelta(days=4)).replace(day=1)
    elif alvo <= local:
        alvo += timedelta(days=1)
    return alvo - fuso


def validar(nome, periodo_dias, frequencia, hora, destinatarios):
    nome = " ".join((nome or "").split())
    if not nome:
        raise ValueError("Informe o nome do relatório.")
    if len(nome) > LIMITE_NOME:
        raise ValueError(f"O nome pode ter até {LIMITE_NOME} caracteres.")
    try:
        periodo_dias, hora = int(periodo_dias), int(hora)
    except (TypeError, ValueError):
        raise ValueError("Período ou horário inválido.")
    if periodo_dias not in {d for d, _ in PERIODOS}:
        raise ValueError("Escolha um período válido.")
    if frequencia not in {f for f, _ in FREQUENCIAS}:
        raise ValueError("Escolha uma frequência válida.")
    if not 0 <= hora <= 23:
        raise ValueError("Escolha um horário entre 0h e 23h.")
    emails = email_envio.separar_emails(destinatarios)
    if not emails:
        raise ValueError("Informe pelo menos um destinatário.")
    return nome, periodo_dias, frequencia, hora, ", ".join(emails)


def _preparar(r):
    if r:
        r["frequencia_texto"] = dict(FREQUENCIAS).get(r["frequencia"], r["frequencia"])
        r["periodo_texto"] = dict(PERIODOS).get(r["periodo_dias"], f"{r['periodo_dias']} dias")
        r["tipo_texto"] = TIPOS.get(r["tipo"], r["tipo"])
    return r


def listar():
    with _conectar() as con, con.cursor() as cur:
        cur.execute("SELECT * FROM relatorios_agendados ORDER BY nome")
        return [_preparar(r) for r in cur.fetchall()]


def obter(relatorio_id):
    with _conectar() as con, con.cursor() as cur:
        cur.execute("SELECT * FROM relatorios_agendados WHERE id = %s", (relatorio_id,))
        return _preparar(cur.fetchone())


def criar(nome, periodo_dias, frequencia, hora, destinatarios):
    with _conectar() as con, con.cursor() as cur:
        cur.execute(
            "INSERT INTO relatorios_agendados (nome, tipo, periodo_dias, frequencia, hora, destinatarios, ativo, proxima_em, criado_em)"
            " VALUES (%s, 'auditoria', %s, %s, %s, %s, 1, %s, UTC_TIMESTAMP())",
            (nome, periodo_dias, frequencia, hora, destinatarios, proxima_execucao(frequencia, hora)),
        )


def atualizar(relatorio_id, nome, periodo_dias, frequencia, hora, destinatarios, ativo):
    with _conectar() as con, con.cursor() as cur:
        cur.execute(
            "UPDATE relatorios_agendados SET nome = %s, periodo_dias = %s, frequencia = %s, hora = %s,"
            " destinatarios = %s, ativo = %s, proxima_em = %s WHERE id = %s",
            (nome, periodo_dias, frequencia, hora, destinatarios, 1 if ativo else 0,
             proxima_execucao(frequencia, hora), relatorio_id),
        )


def apagar(relatorio_id):
    with _conectar() as con, con.cursor() as cur:
        cur.execute("DELETE FROM relatorios_agendados WHERE id = %s", (relatorio_id,))


def vencidos():
    with _conectar() as con, con.cursor() as cur:
        cur.execute("SELECT * FROM relatorios_agendados WHERE ativo = 1 AND proxima_em <= UTC_TIMESTAMP() ORDER BY id")
        return cur.fetchall()


def marcar_envio(relatorio, status):
    """Grava o resultado e agenda o próximo (mesmo se falhou: não insiste a cada minuto)."""
    with _conectar() as con, con.cursor() as cur:
        cur.execute(
            "UPDATE relatorios_agendados SET ultimo_envio = UTC_TIMESTAMP(), ultimo_status = %s, proxima_em = %s WHERE id = %s",
            (status[:500], proxima_execucao(relatorio["frequencia"], relatorio["hora"]), relatorio["id"]),
        )


def enviar(relatorio, conf=None):
    """Monta e envia um relatório agora. Levanta RuntimeError se falhar."""
    conf = conf or configuracoes.ler_todas()
    assunto, html, anexos = montar_relatorio_auditoria(relatorio["periodo_dias"], conf.get("url_base", ""))
    email_envio.enviar(
        email_envio.separar_emails(relatorio["destinatarios"]), assunto, html,
        origem=f"relatorio:{relatorio['id']}", anexos=anexos, conf=conf,
    )
