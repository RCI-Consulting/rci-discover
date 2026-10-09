"""Controle de acesso da área de administração (/admin).

Por enquanto o app roda só na rede interna e ainda não tem login, então
`eh_admin()` libera todo mundo. Quando o login com Microsoft entrar (PR #3),
só esta função muda: ela passa a conferir o papel/grupo do usuário na sessão.
Todas as rotas /admin e o link "Administração" do menu já passam por aqui.
"""


def eh_admin():
    return True
