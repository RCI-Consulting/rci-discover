"""Fila de indexação persistida no MySQL.

Cada upload vira uma linha em `jobs_indexacao`. Os workers (worker.py) pegam
a mais antiga com status 'fila', chamam o Docling e marcam como 'concluido'
ou 'erro'. Como o estado fica no banco, um restart/crash do container não
perde a fila: jobs que estavam 'processando' sem dar sinal de vida voltam
para a fila (até JOBS_MAX_TENTATIVAS vezes).
"""

import config
from db import conectar

_DDL = """
CREATE TABLE IF NOT EXISTS jobs_indexacao (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    indice        VARCHAR(255)  NOT NULL,
    nome_arquivo  VARCHAR(512)  NOT NULL,
    caminho       VARCHAR(1024) NOT NULL,
    doc_id        CHAR(64)      NOT NULL,
    status        VARCHAR(20)   NOT NULL DEFAULT 'fila',
    tentativas    INT           NOT NULL DEFAULT 0,
    mensagem      TEXT          NULL,
    criado_em     DATETIME      NOT NULL,
    iniciado_em   DATETIME      NULL,
    atualizado_em DATETIME      NULL,
    finalizado_em DATETIME      NULL,
    INDEX idx_status_id (status, id),
    INDEX idx_indice_doc (indice, doc_id)
) CHARACTER SET utf8mb4
"""


def criar_tabela():
    with conectar() as con, con.cursor() as cur:
        cur.execute(_DDL)


def job_ativo(indice, doc_id):
    """Já existe um job na fila/processando para esse mesmo documento?"""
    with conectar() as con, con.cursor() as cur:
        cur.execute(
            "SELECT id FROM jobs_indexacao WHERE indice=%s AND doc_id=%s "
            "AND status IN ('fila','processando') LIMIT 1",
            (indice, doc_id),
        )
        return cur.fetchone() is not None


def enfileirar(indice, nome_arquivo, caminho, doc_id):
    with conectar() as con, con.cursor() as cur:
        cur.execute(
            "INSERT INTO jobs_indexacao (indice, nome_arquivo, caminho, doc_id, status, criado_em) "
            "VALUES (%s, %s, %s, %s, 'fila', UTC_TIMESTAMP())",
            (indice, nome_arquivo, str(caminho), doc_id),
        )
        return cur.lastrowid


def enfileirar_lote(indice, itens):
    """Enfileira vários documentos de uma vez (usado pelo crawler). `itens` é
    uma lista de (nome_arquivo, caminho, doc_id); os que já têm job na fila ou
    processando são pulados. Devolve quantos foram pulados."""
    if not itens:
        return 0
    with conectar() as con, con.cursor() as cur:
        ativos = set()
        for i in range(0, len(itens), 500):
            parte = [doc_id for _n, _c, doc_id in itens[i:i + 500]]
            cur.execute(
                "SELECT doc_id FROM jobs_indexacao WHERE indice=%s AND status IN ('fila','processando') "
                "AND doc_id IN (" + ",".join(["%s"] * len(parte)) + ")",
                (indice, *parte),
            )
            ativos |= {linha["doc_id"] for linha in cur.fetchall()}
        novos = [(indice, nome, str(caminho), doc_id) for nome, caminho, doc_id in itens if doc_id not in ativos]
        for i in range(0, len(novos), 500):
            cur.executemany(
                "INSERT INTO jobs_indexacao (indice, nome_arquivo, caminho, doc_id, status, criado_em) "
                "VALUES (%s, %s, %s, %s, 'fila', UTC_TIMESTAMP())",
                novos[i:i + 500],
            )
    return len(itens) - len(novos)


def listar(limite=100):
    with conectar() as con, con.cursor() as cur:
        cur.execute("SELECT * FROM jobs_indexacao ORDER BY id DESC LIMIT %s", (limite,))
        return cur.fetchall()


def contagem_por_status():
    with conectar() as con, con.cursor() as cur:
        cur.execute("SELECT status, COUNT(*) AS total FROM jobs_indexacao GROUP BY status")
        return {linha["status"]: linha["total"] for linha in cur.fetchall()}


def reivindicar():
    """Pega o job mais antigo da fila de forma atômica (vários workers ou
    processos podem chamar ao mesmo tempo: só um consegue o UPDATE)."""
    with conectar() as con, con.cursor() as cur:
        cur.execute("SELECT id FROM jobs_indexacao WHERE status='fila' ORDER BY id LIMIT 1")
        candidato = cur.fetchone()
        if not candidato:
            return None
        cur.execute(
            "UPDATE jobs_indexacao SET status='processando', tentativas=tentativas+1, "
            "mensagem=NULL, iniciado_em=UTC_TIMESTAMP(), atualizado_em=UTC_TIMESTAMP() "
            "WHERE id=%s AND status='fila'",
            (candidato["id"],),
        )
        if cur.rowcount != 1:
            return None
        cur.execute("SELECT * FROM jobs_indexacao WHERE id=%s", (candidato["id"],))
        return cur.fetchone()


def batimento(job_id):
    with conectar() as con, con.cursor() as cur:
        cur.execute(
            "UPDATE jobs_indexacao SET atualizado_em=UTC_TIMESTAMP() WHERE id=%s AND status='processando'",
            (job_id,),
        )


def concluir(job_id):
    with conectar() as con, con.cursor() as cur:
        cur.execute(
            "UPDATE jobs_indexacao SET status='concluido', mensagem=NULL, finalizado_em=UTC_TIMESTAMP() WHERE id=%s",
            (job_id,),
        )


def falhar(job_id, mensagem):
    with conectar() as con, con.cursor() as cur:
        cur.execute(
            "UPDATE jobs_indexacao SET status='erro', mensagem=%s, finalizado_em=UTC_TIMESTAMP() WHERE id=%s",
            (str(mensagem)[:2000], job_id),
        )


def recuperar_travados(segundos_sem_sinal=120):
    """Jobs 'processando' sem batimento há um tempo = o processo que os
    executava morreu. Voltam para a fila, ou viram erro se já estouraram o
    limite de tentativas (protege contra um documento que derruba o servidor
    em loop)."""
    with conectar() as con, con.cursor() as cur:
        cur.execute(
            "UPDATE jobs_indexacao SET status='erro', finalizado_em=UTC_TIMESTAMP(), "
            "mensagem='Interrompido (servidor reiniciou ou caiu) e o limite de tentativas foi atingido.' "
            "WHERE status='processando' AND atualizado_em < UTC_TIMESTAMP() - INTERVAL %s SECOND "
            "AND tentativas >= %s",
            (segundos_sem_sinal, config.JOBS_MAX_TENTATIVAS),
        )
        cur.execute(
            "UPDATE jobs_indexacao SET status='fila', mensagem='Interrompido; recolocado na fila.' "
            "WHERE status='processando' AND atualizado_em < UTC_TIMESTAMP() - INTERVAL %s SECOND",
            (segundos_sem_sinal,),
        )


def tentar_novamente(job_id):
    with conectar() as con, con.cursor() as cur:
        cur.execute(
            "UPDATE jobs_indexacao SET status='fila', tentativas=0, mensagem=NULL, "
            "iniciado_em=NULL, atualizado_em=NULL, finalizado_em=NULL WHERE id=%s AND status='erro'",
            (job_id,),
        )
        return cur.rowcount == 1


def limpar_concluidos():
    with conectar() as con, con.cursor() as cur:
        cur.execute("DELETE FROM jobs_indexacao WHERE status='concluido'")
        return cur.rowcount


def apagar_do_indice(indice):
    with conectar() as con, con.cursor() as cur:
        cur.execute("DELETE FROM jobs_indexacao WHERE indice=%s", (indice,))
        return cur.rowcount
