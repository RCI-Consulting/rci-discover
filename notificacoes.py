"""E-mails automáticos: relatórios agendados e alertas do sistema (e, na
próxima etapa, os alertas das buscas salvas).

Um agendador em segundo plano acorda a cada minuto e:
- envia os relatórios agendados cujo horário chegou (relatorios.py);
- a cada ALERTAS_A_CADA, procura problemas novos — passada de fonte com erro,
  documento com erro na fila de indexação, índice com saúde vermelha — e manda
  um único e-mail com tudo o que ainda não tinha sido avisado.

Cada problema avisado fica em `alertas_enviados` (pela chave, ex.:
`job:123`), então ninguém recebe o mesmo alerta duas vezes. Se o envio
falhar, o problema não é marcado e entra no próximo e-mail. Com vários
processos, só um roda o agendador por vez (GET_LOCK do MySQL).
"""

import os
import threading
import time
from datetime import datetime, timezone
from html import escape

import configuracoes
import email_envio
import fontes
import indices
import jobs
import relatorios
from db import conectar
from opensearch_client import OpenSearchClient

INTERVALO = 60                      # s entre voltas do agendador
ALERTAS_A_CADA = 600                # s entre verificações de problemas
JANELA_HORAS = 48                   # problemas mais antigos que isso não são avisados
GUARDAR_DIAS = 30                   # por quanto tempo lembrar o que já foi avisado

_DDL = """
CREATE TABLE IF NOT EXISTS alertas_enviados (
    chave      VARCHAR(200) PRIMARY KEY,
    enviado_em DATETIME     NOT NULL
) CHARACTER SET utf8mb4
"""

_iniciado = False
_trava = threading.Lock()
_ultima_verificacao = 0.0
_cliente = None


def _opensearch():
    global _cliente
    if _cliente is None:
        _cliente = OpenSearchClient()
    return _cliente


# ---- Alertas do sistema ------------------------------------------------------

def _problemas(conf):
    """[(chave, grupo, texto)] dos problemas recentes que os alertas cobrem."""
    achados = []
    if conf["alertas_fontes"] == "1":
        with fontes.conectar_fontes() as con, con.cursor() as cur:
            cur.execute(
                "SELECT e.id, e.mensagem, f.nome FROM fonte_execucoes e JOIN fontes f ON f.id = e.fonte_id"
                " WHERE e.status = 'erro' AND e.finalizado_em >= UTC_TIMESTAMP() - INTERVAL %s HOUR ORDER BY e.id",
                (JANELA_HORAS,),
            )
            for linha in cur.fetchall():
                achados.append((f"fonte_exec:{linha['id']}", "Fontes com erro",
                                f"<strong>{escape(linha['nome'])}</strong>: {escape(linha['mensagem'] or 'erro')}"))
    if conf["alertas_fila"] == "1":
        jobs.criar_tabela()
        with conectar() as con, con.cursor() as cur:
            cur.execute(
                "SELECT id, indice, nome_arquivo, mensagem FROM jobs_indexacao"
                " WHERE status = 'erro' AND finalizado_em >= UTC_TIMESTAMP() - INTERVAL %s HOUR ORDER BY id",
                (JANELA_HORAS,),
            )
            for linha in cur.fetchall():
                achados.append((f"job:{linha['id']}", "Documentos com erro na indexação",
                                f"<strong>{escape(linha['nome_arquivo'])}</strong> ({escape(indices.nome_exibicao(linha['indice']))}): "
                                f"{escape((linha['mensagem'] or 'erro')[:300])}"))
    if conf["alertas_indices"] == "1":
        hoje = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for linha in _opensearch().listar_indices(indices.padrao_listagem()):
            nome = linha.get("index", "")
            if indices.eh_gerenciado(nome) and linha.get("health") == "red":
                achados.append((f"indice:{nome}:red:{hoje}", "Índices com saúde vermelha",
                                f"<strong>{escape(indices.nome_exibicao(nome))}</strong>: parte dos dados está indisponível no OpenSearch."))
    return achados


def verificar_alertas(conf=None, forcar=False):
    """Manda um e-mail com os problemas ainda não avisados. Devolve quantos
    foram avisados (0 se não havia nada novo ou se os alertas estão desligados)."""
    conf = conf or configuracoes.ler_todas()
    if conf["alertas_ativos"] != "1" and not forcar:
        return 0
    destinatarios = email_envio.separar_emails(conf["alertas_destinatarios"])
    if not destinatarios:
        return 0
    with conectar() as con, con.cursor() as cur:
        cur.execute(_DDL)
        cur.execute("DELETE FROM alertas_enviados WHERE enviado_em < UTC_TIMESTAMP() - INTERVAL %s DAY", (GUARDAR_DIAS,))
        cur.execute("SELECT chave FROM alertas_enviados")
        ja_avisados = {linha["chave"] for linha in cur.fetchall()}

    novos = [p for p in _problemas(conf) if p[0] not in ja_avisados]
    if not novos:
        return 0

    grupos = {}
    for _chave, grupo, texto in novos:
        grupos.setdefault(grupo, []).append(texto)
    url = conf.get("url_base", "").rstrip("/")
    partes = []
    for grupo, itens in grupos.items():
        lista = "".join(f"<li style='margin-bottom:4px'>{t}</li>" for t in itens[:50])
        mais = f"<p style='color:#5f6b7a'>… e mais {len(itens) - 50}.</p>" if len(itens) > 50 else ""
        partes.append(f"<h3 style='color:#8a1f1f;margin-bottom:4px'>{grupo} ({len(itens)})</h3><ul>{lista}</ul>{mais}")
    links = (f"<p><a href='{escape(url)}/admin/fontes'>Fontes</a> · <a href='{escape(url)}/admin/fila'>Fila</a> · "
             f"<a href='{escape(url)}/admin/indices'>Índices</a></p>" if url else "")
    html = (f"<div style='font-family:Arial,Helvetica,sans-serif;color:#1a2333;max-width:640px'>"
            f"<h2 style='color:#03143b'>Alertas do RCISearch</h2>{''.join(partes)}{links}"
            f"<p style='color:#5f6b7a;font-size:12px'>Cada problema é avisado uma vez só. "
            f"Para mudar quem recebe, use Administração → E-mail.</p></div>")
    email_envio.enviar(destinatarios, f"RCISearch — {len(novos)} alerta(s) novo(s)", html, origem="alertas", conf=conf)

    with conectar() as con, con.cursor() as cur:
        cur.executemany(
            "INSERT IGNORE INTO alertas_enviados (chave, enviado_em) VALUES (%s, UTC_TIMESTAMP())",
            [(chave,) for chave, _g, _t in novos],
        )
    return len(novos)


# ---- Agendador --------------------------------------------------------------

def _uma_volta():
    global _ultima_verificacao
    conf = configuracoes.ler_todas()
    if not email_envio.configurado(conf):
        return
    for relatorio in relatorios.vencidos():
        try:
            relatorios.enviar(relatorio, conf)
            relatorios.marcar_envio(relatorio, "Enviado")
        except Exception as e:
            relatorios.marcar_envio(relatorio, f"Erro: {e}")
    if time.monotonic() - _ultima_verificacao >= ALERTAS_A_CADA:
        _ultima_verificacao = time.monotonic()
        try:
            verificar_alertas(conf)
        except Exception as e:
            print(f"[Notificações] Falha nos alertas do sistema: {e}")


def _loop():
    while True:
        try:
            with conectar() as trava, trava.cursor() as cur:  # um processo por vez
                cur.execute("SELECT GET_LOCK('rcisearch_notificacoes', 0) AS ok")
                if cur.fetchone()["ok"]:
                    try:
                        _uma_volta()
                    finally:
                        cur.execute("SELECT RELEASE_LOCK('rcisearch_notificacoes')")
        except Exception as e:
            print(f"[Notificações] Banco indisponível ou erro no agendador: {e}")
        time.sleep(INTERVALO)


def iniciar():
    global _iniciado
    if os.getenv("NOTIFICACOES_DESATIVADAS") == "1":
        print("[Notificações] Agendador desativado (NOTIFICACOES_DESATIVADAS=1).")
        return
    with _trava:
        if _iniciado:
            return
        _iniciado = True
    threading.Thread(target=_loop, name="notificacoes", daemon=True).start()
    print("[Notificações] Agendador iniciado.")
