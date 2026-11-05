"""Prepara o banco MySQL quando o app sobe: cria o banco (se faltar e o usuário
tiver permissão), todas as tabelas e o primeiro administrador.

Cada módulo continua criando a sua tabela no primeiro uso (como antes), mas
agora tudo é criado logo no deploy, e o log mostra na hora se o banco está
acessível e o que falhou. Os comandos são `CREATE TABLE IF NOT EXISTS`, então
rodar de novo não apaga nem altera dados.

No log do Dokploy aparece algo como:
    [Banco] Conectado em mysql-interno:3306, banco "rcisearch", usuário "discover".
    [Banco] 17 tabelas prontas.

Também dá para rodar à mão, dentro do container:
    python banco.py
"""

import threading
import time

import pymysql

import config
from db import conectar

ESPERA_MAXIMA = 90      # s esperando o MySQL subir (no deploy ele pode vir depois do app)
INTERVALO = 5           # s entre tentativas
ERRO_BANCO_INEXISTENTE = 1049


def _destino():
    return (f'{config.MYSQL_HOST}:{config.MYSQL_PORT}, banco "{config.MYSQL_DATABASE}", '
            f'usuário "{config.MYSQL_USER}"')


def _criar_banco():
    """Cria o banco quando ele não existe (precisa da permissão CREATE)."""
    con = pymysql.connect(host=config.MYSQL_HOST, port=config.MYSQL_PORT, user=config.MYSQL_USER,
                          password=config.MYSQL_PASSWORD, charset="utf8mb4", autocommit=True, connect_timeout=5)
    with con, con.cursor() as cur:
        nome = config.MYSQL_DATABASE.replace("`", "")
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{nome}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    print(f'[Banco] Banco "{config.MYSQL_DATABASE}" criado.')


def _esperar_conexao():
    """True quando conecta; False se o tempo acabou (o motivo vai para o log)."""
    limite = time.monotonic() + ESPERA_MAXIMA
    ultimo_erro = None
    while True:
        try:
            with conectar():
                return True
        except pymysql.err.OperationalError as e:
            if e.args and e.args[0] == ERRO_BANCO_INEXISTENTE:
                try:
                    _criar_banco()
                    continue
                except Exception as e2:
                    print(f'[Banco] O banco "{config.MYSQL_DATABASE}" não existe e não consegui criá-lo: {e2}. '
                          "Crie o banco ou dê permissão CREATE ao usuário.")
                    return False
            ultimo_erro = e
        except Exception as e:
            ultimo_erro = e
        if time.monotonic() >= limite:
            print(f"[Banco] Não consegui conectar em {_destino()}: {ultimo_erro}. "
                  "Confira MYSQL_HOST (nome interno do serviço no Dokploy), MYSQL_PORT, MYSQL_USER, "
                  "MYSQL_PASSWORD e MYSQL_DATABASE.")
            return False
        time.sleep(INTERVALO)


def _etapas():
    """(tabelas, função que as cria) de cada módulo, usando as funções que os
    próprios módulos já têm, para não haver duas definições da mesma tabela."""
    import auditoria
    import buscas_salvas
    import configuracoes
    import destaques
    import dicionario
    import email_envio
    import fontes
    import jobs
    import notificacoes
    import pastas
    import projetos
    import relatorios
    import usuarios

    def com_conexao(garantir):
        def criar():
            with conectar() as con:
                garantir(con)
        return criar

    def pelo_conectar(conectar_modulo):  # módulos cujo _conectar() já cria a tabela
        def criar():
            with conectar_modulo():
                pass
        return criar

    def ddl(sql):
        def criar():
            with conectar() as con, con.cursor() as cur:
                cur.execute(sql)
        return criar

    return [
        (["usuarios"], com_conexao(usuarios._garantir_tabela)),
        (["jobs_indexacao"], jobs.criar_tabela),
        (["auditoria_buscas"], com_conexao(auditoria._garantir_tabela)),
        (["projetos", "projeto_indices"], com_conexao(projetos._garantir_tabelas)),
        (["dicionario_termos"], com_conexao(dicionario._garantir_tabela)),
        (["destaques"], com_conexao(destaques._garantir_tabela)),
        (["fontes", "fonte_itens", "fonte_execucoes"], com_conexao(fontes._garantir_tabelas)),
        (["pastas", "pasta_itens"], pelo_conectar(pastas._conectar)),
        (["buscas_salvas"], pelo_conectar(buscas_salvas._conectar)),
        (["configuracoes"], pelo_conectar(configuracoes._conectar)),
        (["emails_enviados"], pelo_conectar(email_envio._conectar)),
        (["relatorios_agendados"], pelo_conectar(relatorios._conectar)),
        (["alertas_enviados"], ddl(notificacoes._DDL)),
    ]


def preparar():
    """Conecta, cria as tabelas e o 1º admin. Devolve True se tudo deu certo.
    Nunca levanta exceção: o app sobe mesmo com o banco fora (a busca não
    depende dele) e o log explica o problema."""
    if not _esperar_conexao():
        return False
    print(f"[Banco] Conectado em {_destino()}.")

    prontas, falhas = 0, []
    for tabelas, criar in _etapas():
        try:
            criar()
            prontas += len(tabelas)
        except Exception as e:
            falhas.append(f"{', '.join(tabelas)}: {e}")
    if falhas:
        print(f"[Banco] {prontas} tabelas prontas; {len(falhas)} falharam (o usuário precisa das permissões "
              "CREATE, ALTER, INDEX e REFERENCES):")
        for falha in falhas:
            print(f"[Banco]   - {falha}")
    else:
        print(f"[Banco] {prontas} tabelas prontas.")

    try:
        import usuarios
        usuarios.garantir_admin_inicial()
        if usuarios.contar() == 0:
            print("[Banco] Nenhum usuário cadastrado: defina ADMIN_EMAIL e ADMIN_SENHA e reinicie para criar o administrador.")
    except Exception as e:
        print(f"[Banco] Não consegui verificar os usuários: {e}")
    return not falhas


def iniciar():
    """Roda o preparar() em segundo plano, para o app começar a responder na
    hora mesmo enquanto espera o MySQL."""
    threading.Thread(target=preparar, name="preparar-banco", daemon=True).start()


if __name__ == "__main__":
    raise SystemExit(0 if preparar() else 1)
