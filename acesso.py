"""Quem está logado e o que pode fazer.

O login é local (usuarios.py): a sessão guarda `session["usuario"]` com id,
e-mail, nome e papel. A cada requisição o web_app confere no banco se o
usuário continua ativo e com o mesmo papel (g.usuario); se o banco estiver
fora, vale o que está na sessão.

Todas as rotas /admin e o link "Administração" do menu passam por
`eh_admin()`. Buscas salvas, pastas e auditoria usam `usuario_atual()`.
"""

from flask import g, has_request_context, session


def _usuario():
    if not has_request_context():
        return None
    usuario = getattr(g, "usuario", None)
    if usuario is None:
        usuario = session.get("usuario")
    return usuario if isinstance(usuario, dict) else None


def logado():
    return _usuario() is not None


def eh_admin():
    usuario = _usuario()
    return bool(usuario) and usuario.get("papel") == "admin"


def usuario_atual():
    """E-mail de quem está logado, ou None."""
    usuario = _usuario()
    return (usuario.get("email") or usuario.get("nome")) if usuario else None


def nome_atual():
    usuario = _usuario()
    return (usuario.get("nome") or usuario.get("email")) if usuario else None
