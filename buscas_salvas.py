"""Buscas salvas e alertas de documentos novos (como as buscas salvas e
alertas do Watson Explorer).

O usuário salva a busca da tela (termo, filtros, temas, ordem e projeto) e
pode pedir um e-mail diário ou semanal com os documentos que entraram ou
foram atualizados desde o último aviso. O agendador (notificacoes.py) chama
`processar_alertas` a cada minuto.

Enquanto o login não entra, o dono de toda busca é `None` (todo mundo vê as
mesmas buscas salvas). Com o login, `acesso.usuario_atual()` passa a devolver
o e-mail de quem está logado e cada um vê só as suas.
"""

import threading
from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import parse_qsl, quote, urlencode

from werkzeug.datastructures import MultiDict

import config
import dicionario
import email_envio
import facetas
import indices
import projetos
import relatorios
import relevancia
import temas
from db import conectar

_DDL = """
CREATE TABLE IF NOT EXISTS buscas_salvas (
    id               BIGINT AUTO_INCREMENT PRIMARY KEY,
    dono             VARCHAR(255)  NULL,
    nome             VARCHAR(100)  NOT NULL,
    q                VARCHAR(1000) NOT NULL,
    parametros       VARCHAR(2000) NOT NULL,
    projeto          VARCHAR(50)   NULL,
    alerta_email     VARCHAR(255)  NULL,
    frequencia       VARCHAR(20)   NOT NULL DEFAULT 'nenhuma',
    desde            DATETIME      NOT NULL,
    proxima_em       DATETIME      NULL,
    ultimo_status    VARCHAR(500)  NULL,
    criado_em        DATETIME      NOT NULL,
    INDEX idx_dono (dono),
    INDEX idx_proxima (frequencia, proxima_em)
) CHARACTER SET utf8mb4
"""

FREQUENCIAS = [("nenhuma", "Sem alerta"), ("diaria", "Todo dia"), ("semanal", "Toda segunda-feira")]
HORA_ALERTA = 7                 # horário de Brasília dos e-mails
PARAMETROS_GUARDADOS = ("indice", "tipo", "data", "tema", "ordem")
LIMITE_NOME = 100
MAX_DOCS_EMAIL = 25

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


def _agora():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _proxima(frequencia, agora=None):
    """Mesma regra de agenda dos relatórios (hora cheia em Brasília)."""
    if frequencia == "nenhuma":
        return None
    return relatorios.proxima_execucao(frequencia, HORA_ALERTA, agora)


def parametros_da_tela(args):
    """Só o que define a busca (filtros, temas e ordem), sem `q` nem página."""
    return urlencode([(k, v) for k, v in args.items(multi=True) if k in PARAMETROS_GUARDADOS])[:2000]


def link(busca, base=""):
    """Endereço que roda a busca salva de novo."""
    caminho = f"/p/{quote(busca['projeto'])}" if busca.get("projeto") else "/busca"
    extra = f"&{busca['parametros']}" if busca.get("parametros") else ""
    return f"{base.rstrip('/')}{caminho}?q={quote(busca['q'])}{extra}"


def validar(nome, frequencia, alerta_email):
    nome = " ".join((nome or "").split())
    if not nome:
        raise ValueError("Dê um nome para a busca.")
    if len(nome) > LIMITE_NOME:
        raise ValueError(f"O nome pode ter até {LIMITE_NOME} caracteres.")
    if frequencia not in {f for f, _ in FREQUENCIAS}:
        raise ValueError("Escolha a frequência do alerta.")
    emails = email_envio.separar_emails(alerta_email) if alerta_email else []
    if len(emails) > 1:
        raise ValueError("Informe um e-mail só para o alerta.")
    if frequencia != "nenhuma" and not emails:
        raise ValueError("Informe o e-mail que recebe o alerta.")
    return nome, frequencia, (emails[0] if emails else None)


def _preparar(b):
    if b:
        b["frequencia_texto"] = dict(FREQUENCIAS).get(b["frequencia"], b["frequencia"])
        b["link"] = link(b)
        filtros = MultiDict(parse_qsl(b["parametros"]))
        partes = [facetas.resumo_texto(facetas.ler_filtros(filtros)), temas.resumo_texto(temas.ler_temas(filtros))]
        b["filtros_texto"] = "; ".join(p for p in partes if p)
    return b


def listar(dono):
    with _conectar() as con, con.cursor() as cur:
        cur.execute("SELECT * FROM buscas_salvas WHERE dono <=> %s ORDER BY nome", (dono,))
        return [_preparar(b) for b in cur.fetchall()]


LIMITE_TODAS = 500


def todas(dono=None, sem_dono=False):
    """Buscas salvas de todo mundo, mais recentes primeiro (visão do
    administrador). Com `dono`, só as dessa pessoa; com `sem_dono`, só as
    criadas antes do login. No máximo LIMITE_TODAS."""
    sql, params = "SELECT * FROM buscas_salvas", ()
    if sem_dono:
        sql += " WHERE dono IS NULL"
    elif dono:
        sql, params = sql + " WHERE dono = %s", (dono,)
    with _conectar() as con, con.cursor() as cur:
        cur.execute(f"{sql} ORDER BY criado_em DESC LIMIT {LIMITE_TODAS}", params)
        return [_preparar(b) for b in cur.fetchall()]


def obter(busca_id, dono):
    """A busca, se for desse dono (ninguém mexe na busca salva de outro)."""
    with _conectar() as con, con.cursor() as cur:
        cur.execute("SELECT * FROM buscas_salvas WHERE id = %s AND dono <=> %s", (busca_id, dono))
        return _preparar(cur.fetchone())


def criar(dono, nome, q, parametros, projeto, frequencia, alerta_email):
    q = " ".join((q or "").split())[:1000]
    if not q:
        raise ValueError("Não há busca para salvar.")
    nome, frequencia, alerta_email = validar(nome, frequencia, alerta_email)
    with _conectar() as con, con.cursor() as cur:
        cur.execute(
            "INSERT INTO buscas_salvas (dono, nome, q, parametros, projeto, alerta_email, frequencia, desde, proxima_em, criado_em)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP(), %s, UTC_TIMESTAMP())",
            (dono, nome, q, parametros or "", projeto or None, alerta_email, frequencia, _proxima(frequencia)),
        )
        return cur.lastrowid


def atualizar(busca_id, dono, nome, frequencia, alerta_email):
    nome, frequencia, alerta_email = validar(nome, frequencia, alerta_email)
    with _conectar() as con, con.cursor() as cur:
        cur.execute(
            # ligar o alerta agora não manda o que entrou enquanto ele estava desligado
            "UPDATE buscas_salvas SET desde = IF(frequencia = 'nenhuma' AND %s <> 'nenhuma', UTC_TIMESTAMP(), desde),"
            " nome = %s, frequencia = %s, alerta_email = %s, proxima_em = %s WHERE id = %s AND dono <=> %s",
            (frequencia, nome, frequencia, alerta_email, _proxima(frequencia), busca_id, dono),
        )


def apagar(busca_id, dono):
    with _conectar() as con, con.cursor() as cur:
        cur.execute("DELETE FROM buscas_salvas WHERE id = %s AND dono <=> %s", (busca_id, dono))


# ---- Alertas ----------------------------------------------------------------

def _alvo(busca):
    """Índices onde a busca roda: os do projeto, ou todos."""
    if busca.get("projeto"):
        projeto = projetos.obter_por_slug(busca["projeto"])
        nomes = [i for i in (projeto or {}).get("indices", []) if indices.eh_gerenciado(i)]
        return ",".join(nomes)
    return indices.padrao_busca()


def documentos_novos(cliente, busca, desde, limite=MAX_DOCS_EMAIL):
    """(total, [docs]) que casam com a busca salva e foram indexados depois de
    `desde` — a mesma consulta da tela, com os mesmos filtros e temas."""
    alvo = _alvo(busca)
    if not alvo:
        return 0, []
    args = MultiDict(parse_qsl(busca["parametros"]))
    q = busca["q"]
    try:
        expansoes = dicionario.expandir(q)
    except Exception:
        expansoes = []
    consulta = temas.restringir(relevancia.montar_consulta(q, expansoes), temas.ler_temas(args))
    filtro_facetas, _aggs = facetas.montar_consulta(facetas.ler_filtros(args))
    filtros = [{"range": {"indexado_em": {"gt": desde.strftime("%Y-%m-%dT%H:%M:%SZ")}}}]
    if filtro_facetas:
        filtros.append(filtro_facetas)
    resp = cliente.search(alvo, {
        "size": limite,
        "query": {"bool": {"must": [consulta], "filter": filtros}},
        "sort": [{"indexado_em": {"order": "desc", "unmapped_type": "date"}}],
        "_source": ["nome", "titulo"],
    }, ignore_unavailable=True)
    docs = [
        {"indice": h["_index"], "id": h["_id"], "nome": h["_source"].get("nome", "(sem nome)"),
         "titulo": h["_source"].get("titulo") or h["_source"].get("nome", "(sem nome)")}
        for h in resp["hits"]["hits"]
    ]
    return resp["hits"]["total"]["value"], docs


def _html_alerta(busca, total, docs, base):
    itens = "".join(
        f"<li style='margin-bottom:6px'><a href='{escape(base)}/preview/{quote(d['indice'])}/{quote(d['id'])}'>{escape(d['titulo'])}</a>"
        f"<br><span style='color:#5f6b7a;font-size:12px'>{escape(d['nome'])} · {escape(indices.nome_exibicao(d['indice']))}</span></li>"
        for d in docs
    )
    mais = f"<p style='color:#5f6b7a'>… e mais {total - len(docs)}.</p>" if total > len(docs) else ""
    return (
        "<div style='font-family:Arial,Helvetica,sans-serif;color:#1a2333;max-width:640px'>"
        f"<h2 style='color:#03143b;margin-bottom:4px'>{total} documento(s) novo(s) ou atualizado(s)</h2>"
        f"<p>Na sua busca salva <strong>{escape(busca['nome'])}</strong> (\"{escape(busca['q'])}\").</p>"
        f"<ul style='padding-left:18px'>{itens}</ul>{mais}"
        f"<p><a href='{escape(link(busca, base))}'>Abrir a busca no {escape(config.APP_NOME)}</a> · "
        f"<a href='{escape(base)}/buscas-salvas'>Mudar ou cancelar este alerta</a></p></div>"
    )


def processar_alertas(cliente, conf):
    """Envia os alertas vencidos. Sem documento novo, não manda e-mail (só
    reagenda). Se o envio falhar, não avança o `desde`: os documentos entram
    no próximo aviso."""
    with _conectar() as con, con.cursor() as cur:
        cur.execute(
            "SELECT * FROM buscas_salvas WHERE frequencia <> 'nenhuma' AND alerta_email IS NOT NULL"
            " AND proxima_em <= UTC_TIMESTAMP() ORDER BY id"
        )
        vencidas = cur.fetchall()
    base = (conf.get("url_base") or "").rstrip("/")
    for busca in vencidas:
        inicio = _agora()
        avancar, status = True, "Nada novo"
        try:
            total, docs = documentos_novos(cliente, busca, busca["desde"])
            if total:
                email_envio.enviar(
                    [busca["alerta_email"]], f"{config.APP_NOME} — {total} novo(s) em \"{busca['nome']}\""[:300],
                    _html_alerta(busca, total, docs, base), origem=f"busca_salva:{busca['id']}", conf=conf,
                )
                status = f"Enviado ({total} documento(s))"
        except Exception as e:
            avancar, status = False, f"Erro: {e}"
        with _conectar() as con, con.cursor() as cur:
            cur.execute(
                "UPDATE buscas_salvas SET proxima_em = %s, ultimo_status = %s, desde = IF(%s, %s, desde) WHERE id = %s",
                (_proxima(busca["frequencia"], inicio + timedelta(minutes=1)), status[:500], avancar, inicio, busca["id"]),
            )
