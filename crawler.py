"""Crawler das fontes (fontes.py): o agendador roda cada fonte na hora
marcada (ou quando pedem "Rodar agora") e faz uma passada incremental.

Uma passada numa fonte do tipo `pasta`:
1. lista os arquivos da pasta (e subpastas, se marcado), com a assinatura
   de cada um (data de modificação + tamanho);
2. compara com o que a fonte tinha na passada anterior (`fonte_itens`);
3. coloca na fila de indexação (a mesma do upload, processada pelo Docling)
   só os arquivos **novos** ou **alterados**;
4. apaga do índice os documentos cujos arquivos **sumiram** da pasta.

Proteções: se a pasta não estiver acessível (compartilhamento fora do ar),
vier vazia quando antes tinha arquivos, ou alguma subpasta não puder ser
lida, nada é apagado do índice nessa passada. Os arquivos não são copiados:
o índice guarda o caminho na própria pasta, de onde saem o preview e o download.

Só uma passada por fonte de cada vez, mesmo com vários processos: a trava é
um GET_LOCK do MySQL, liberado sozinho se o processo morrer.
"""

import os
import threading
import time

import fontes
import jobs
from opensearch_client import OpenSearchClient
from utils import gerar_doc_id

INTERVALO_AGENDADOR = 20   # s entre verificações de fontes pendentes
INTERVALO_ERRO = 60        # s de espera quando o banco está indisponível
LOTE = 500                 # linhas por comando no MySQL
LIMITE_CAMINHO = 1000      # a fila guarda até 1024 caracteres
LIMITE_PREVIA = 10000      # arquivos contados no "Testar"

_iniciado = False
_trava = threading.Lock()
_cliente = None


def _opensearch():
    global _cliente
    if _cliente is None:
        _cliente = OpenSearchClient()
    return _cliente


def listar_pasta(conf, problemas):
    """Gera (caminho, nome, assinatura) dos arquivos aceitos pela fonte.
    Subpastas que não puderem ser lidas e arquivos ignorados por motivo
    técnico vão para a lista `problemas`. Levanta RuntimeError se a pasta
    principal não existir ou estiver fora das pastas liberadas."""
    raiz = conf["caminho"]
    if not fontes.pasta_permitida(raiz):
        raise RuntimeError(f"A pasta {raiz} está fora das pastas liberadas para fontes (FONTES_PASTAS_PERMITIDAS).")
    if not os.path.isdir(raiz):
        raise RuntimeError(f"Pasta não encontrada ou inacessível no servidor: {raiz}. O compartilhamento está montado?")
    extensoes = {"." + e for e in conf["extensoes"]}
    padroes = fontes.separar_padroes(conf.get("ignorar"))

    def _erro(e):
        problemas.append(f"Não consegui ler {getattr(e, 'filename', '') or e}")

    for pasta, subpastas, arquivos in os.walk(raiz, onerror=_erro):
        if conf.get("subpastas"):
            subpastas[:] = sorted(s for s in subpastas if not fontes.ignorado(s, padroes))
        else:
            subpastas[:] = []
        for nome in sorted(arquivos):
            if fontes.ignorado(nome, padroes) or os.path.splitext(nome)[1].lower() not in extensoes:
                continue
            caminho = os.path.join(pasta, nome)
            if os.path.islink(caminho):
                continue  # atalhos poderiam apontar para fora da pasta liberada
            if len(caminho) > LIMITE_CAMINHO:
                problemas.append(f"Caminho longo demais, ignorado: {caminho[:120]}…")
                continue
            try:
                info = os.stat(caminho)
            except OSError as e:
                problemas.append(f"Não consegui ler {caminho}: {e.strerror}")
                continue
            yield caminho, nome, f"{int(info.st_mtime)}:{info.st_size}"


def previa(conf, limite=10):
    """Para o botão "Testar": (total encontrado, primeiros caminhos, problemas,
    se a contagem parou no LIMITE_PREVIA). Não indexa nada."""
    problemas, primeiros, total = [], [], 0
    for caminho, _nome, _assinatura in listar_pasta(conf, problemas):
        total += 1
        if len(primeiros) < limite:
            primeiros.append(os.path.relpath(caminho, conf["caminho"]))
        if total >= LIMITE_PREVIA:
            return total, primeiros, problemas[:10], True
    return total, primeiros, problemas[:10], False


def _em_lotes(lista, tamanho=LOTE):
    for i in range(0, len(lista), tamanho):
        yield lista[i:i + tamanho]


def executar(fonte):
    """Uma passada incremental. Devolve o dicionário com as contagens, que
    também fica gravado em `fonte_execucoes`."""
    fid = fonte["id"]
    with fontes.conectar_fontes() as con, con.cursor() as cur:
        cur.execute(
            "INSERT INTO fonte_execucoes (fonte_id, status, iniciado_em) VALUES (%s, 'executando', UTC_TIMESTAMP())",
            (fid,),
        )
        exec_id = cur.lastrowid
    r = {"encontrados": 0, "novos": 0, "alterados": 0, "removidos": 0, "ja_na_fila": 0}
    status, mensagem = "concluida", None
    try:
        with fontes.conectar_fontes() as con, con.cursor() as cur:
            cur.execute("SELECT doc_id, assinatura FROM fonte_itens WHERE fonte_id = %s", (fid,))
            antigos = {linha["doc_id"]: linha["assinatura"] for linha in cur.fetchall()}

        problemas, mudaram, vistos = [], [], set()
        for caminho, nome, assinatura in listar_pasta(fonte["config"], problemas):
            doc_id = gerar_doc_id(caminho)  # o mesmo id do upload: reenviar não duplica
            if doc_id in vistos:
                continue
            vistos.add(doc_id)
            r["encontrados"] += 1
            anterior = antigos.get(doc_id)
            if anterior == assinatura:
                continue
            r["novos" if anterior is None else "alterados"] += 1
            mudaram.append((nome, caminho, doc_id, assinatura))

        sumiram = [doc_id for doc_id in antigos if doc_id not in vistos]
        if sumiram and not vistos:
            raise RuntimeError(
                "A pasta veio vazia, mas antes tinha arquivos. Nada foi apagado do índice por segurança "
                "(o compartilhamento pode estar fora do ar). Se esvaziaram a pasta de propósito, apague a fonte."
            )
        if sumiram and problemas:
            mensagem = "Algumas pastas não puderam ser lidas, então nada foi apagado do índice nesta passada."
            sumiram = []

        # 1) fila primeiro; 2) só então grava a assinatura nova (se cair no meio, a próxima passada repete)
        r["ja_na_fila"] = jobs.enfileirar_lote(fonte["indice"], [(n, c, d) for n, c, d, _ in mudaram])
        with fontes.conectar_fontes() as con, con.cursor() as cur:
            for lote in _em_lotes(mudaram):
                cur.executemany(
                    "INSERT INTO fonte_itens (fonte_id, doc_id, chave, assinatura, visto_em)"
                    " VALUES (%s, %s, %s, %s, UTC_TIMESTAMP())"
                    " ON DUPLICATE KEY UPDATE chave = VALUES(chave), assinatura = VALUES(assinatura), visto_em = VALUES(visto_em)",
                    [(fid, d, c, a) for _n, c, d, a in lote],
                )

        if sumiram:
            _opensearch().apagar_documentos(fonte["indice"], sumiram)
            r["removidos"] = len(sumiram)
        with fontes.conectar_fontes() as con, con.cursor() as cur:
            for lote in _em_lotes(sumiram):
                cur.executemany("DELETE FROM fonte_itens WHERE fonte_id = %s AND doc_id = %s", [(fid, d) for d in lote])

        if problemas:
            extra = f"{len(problemas)} problema(s) de leitura: " + "; ".join(problemas[:5])
            mensagem = f"{mensagem} {extra}" if mensagem else extra
    except Exception as e:
        status, mensagem = "erro", str(e)

    try:
        with fontes.conectar_fontes() as con, con.cursor() as cur:
            cur.execute(
                "UPDATE fonte_execucoes SET status = %s, encontrados = %s, novos = %s, alterados = %s,"
                " removidos = %s, ja_na_fila = %s, mensagem = %s, finalizado_em = UTC_TIMESTAMP() WHERE id = %s",
                (status, r["encontrados"], r["novos"], r["alterados"], r["removidos"], r["ja_na_fila"],
                 (mensagem or "")[:2000] or None, exec_id),
            )
    except Exception as e:
        print(f"[Crawler] Não consegui gravar o resultado da fonte {fid}: {e}")
    print(f"[Crawler] Fonte {fid} ({fonte['nome']}): {status} {r} {mensagem or ''}")
    return {**r, "status": status, "mensagem": mensagem}


def _rodar_com_trava(fonte):
    """Roda a fonte se ninguém mais estiver rodando; reagenda a próxima."""
    fid = fonte["id"]
    with fontes.conectar_fontes() as trava, trava.cursor() as cur:  # conexão segura a trava até o fim
        cur.execute("SELECT GET_LOCK(%s, 0) AS ok", (f"rcisearch_fonte_{fid}",))
        if not cur.fetchone()["ok"]:
            return False
        try:
            # execução "executando" sem dono = processo que morreu no meio
            cur.execute(
                "UPDATE fonte_execucoes SET status = 'interrompida', finalizado_em = UTC_TIMESTAMP(),"
                " mensagem = 'O servidor reiniciou durante a passada.' WHERE fonte_id = %s AND status = 'executando'",
                (fid,),
            )
            cur.execute("UPDATE fontes SET solicitada = 0 WHERE id = %s", (fid,))
            executar(fonte)
            cur.execute(
                "UPDATE fontes SET proxima_em = IF(intervalo_horas > 0, UTC_TIMESTAMP() + INTERVAL intervalo_horas HOUR, NULL)"
                " WHERE id = %s",
                (fid,),
            )
        finally:
            cur.execute("SELECT RELEASE_LOCK(%s)", (f"rcisearch_fonte_{fid}",))
    return True


def rodar_pendentes():
    with fontes.conectar_fontes() as con, con.cursor() as cur:
        cur.execute(
            "SELECT id FROM fontes WHERE solicitada = 1 OR (ativa = 1 AND intervalo_horas > 0"
            " AND (proxima_em IS NULL OR proxima_em <= UTC_TIMESTAMP())) ORDER BY id"
        )
        pendentes = [linha["id"] for linha in cur.fetchall()]
    for fid in pendentes:
        fonte = fontes.obter(fid)
        if fonte:
            _rodar_com_trava(fonte)


def _loop():
    tabela_ok = False
    while True:
        try:
            if not tabela_ok:
                jobs.criar_tabela()  # a fila pode ainda não existir se os workers estiverem desligados
                tabela_ok = True
            rodar_pendentes()
            time.sleep(INTERVALO_AGENDADOR)
        except Exception as e:
            print(f"[Crawler] Banco indisponível ou erro no agendador: {e}")
            time.sleep(INTERVALO_ERRO)


def iniciar():
    """Sobe o agendador (uma vez por processo)."""
    global _iniciado
    if os.getenv("CRAWLER_DESATIVADO") == "1":
        print("[Crawler] Agendador desativado (CRAWLER_DESATIVADO=1).")
        return
    with _trava:
        if _iniciado:
            return
        _iniciado = True
    threading.Thread(target=_loop, name="crawler-agendador", daemon=True).start()
    print("[Crawler] Agendador iniciado.")
