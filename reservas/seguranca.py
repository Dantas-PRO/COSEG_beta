from functools import wraps

from django.http import JsonResponse


def login_obrigatorio(view):
    """Devolve 401 em JSON se o usuário não estiver autenticado."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse(
                {"sucesso": False,
                 "mensagem": "Autenticação necessária. Faça login em /api/login/.",
                 "erros": {}},
                status=401,
            )
        return view(request, *args, **kwargs)
    return wrapper