"""Workers em segundo plano que consomem a fila de indexação (jobs.py)."""

import os
import threading
import time

import config
import jobs
from docling_service import processar_e_indexar_pdf

INTERVALO_OCIOSO = 2          # s entre consultas quando a fila está vazia
INTERVALO_ERRO = 10           # s de espera quando o banco está indisponível
INTERVALO_MANUTENCAO = 30     # s entre varreduras de jobs travados
INTERVALO_BATIMENTO = 30      # s entre sinais de vida de um job em execução
SEM_SINAL_ATE = 120           # s sem batimento para considerar o job travado

_iniciado = False
_trava = threading.Lock()


def iniciar():
    """Sobe os threads (uma vez por processo)."""
    global _iniciado
    if os.getenv("JOBS_DESATIVADOS") == "1":
        print("[Fila] Workers desativados (JOBS_DESATIVADOS=1).")
        return
    with _trava:
        if _iniciado:
            return
        _iniciado = True
    total = max(1, config.JOBS_CONCURRENCIA)
    for n in range(total):
        threading.Thread(target=_loop, args=(n,), name=f"worker-fila-{n}", daemon=True).start()
    print(f"[Fila] {total} worker(s) iniciado(s).")


def _loop(numero):
    tabela_ok = False
    ultima_manutencao = 0.0
    while True:
        try:
            if not tabela_ok:
                jobs.criar_tabela()
                tabela_ok = True
            if numero == 0 and time.monotonic() - ultima_manutencao >= INTERVALO_MANUTENCAO:
                jobs.recuperar_travados(SEM_SINAL_ATE)
                ultima_manutencao = time.monotonic()
            job = jobs.reivindicar()
        except Exception as e:
            print(f"[Fila] Banco indisponível: {e}")
            time.sleep(INTERVALO_ERRO)
            continue

        if not job:
            time.sleep(INTERVALO_OCIOSO)
            continue

        _executar(job)


def _executar(job):
    parar = threading.Event()

    def _bater():
        while not parar.wait(INTERVALO_BATIMENTO):
            try:
                jobs.batimento(job["id"])
            except Exception:
                pass

    threading.Thread(target=_bater, daemon=True).start()
    inicio = time.monotonic()
    print(f"[Fila] Job {job['id']} iniciado: {job['nome_arquivo']} -> {job['indice']}")
    try:
        if not os.path.isfile(job["caminho"]):
            raise RuntimeError("Arquivo não encontrado no servidor (foi removido depois do upload?).")
        processar_e_indexar_pdf(job["caminho"], job["doc_id"], job["indice"])
        resultado = ("concluido", None)
    except Exception as e:
        resultado = ("erro", str(e))
    finally:
        parar.set()

    duracao = time.monotonic() - inicio
    try:
        if resultado[0] == "concluido":
            jobs.concluir(job["id"])
        else:
            jobs.falhar(job["id"], resultado[1])
    except Exception as e:
        print(f"[Fila] Não consegui gravar o resultado do job {job['id']}: {e}")
    print(f"[Fila] Job {job['id']} {resultado[0]} em {duracao:.1f}s")
