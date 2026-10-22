"""Controle de acesso da área de administração (/admin).

Por enquanto o app roda só na rede interna e ainda não tem login, então
`eh_admin()` libera todo mundo. Quando o login com Microsoft entrar (PR #3),
só esta função muda: ela passa a conferir o papel/grupo do usuário na sessão.
Todas as rotas /admin e o link "Administração" do menu já passam por aqui.
"""


def eh_admin():
    return True


def usuario_atual():
    """E-mail de quem está logado, ou None. Sem login (hoje), é sempre None:
    buscas salvas e pastas privadas ficam de um dono "anônimo" compartilhado."""
    from flask import session
    logado = session.get("usuario")
    if isinstance(logado, dict):
        logado = logado.get("email") or logado.get("nome")
    return logado or None
