from django.http import JsonResponse

CODIGOS = {400: "requisicao_invalida", 403: "acesso_negado", 404: "nao_encontrado",
           405: "metodo_nao_permitido", 500: "erro_interno"}
MENSAGENS = {
    400: "Requisição inválida.",
    403: "Acesso negado.",
    404: "Recurso não encontrado.",
    405: "Método não permitido para este endereço.",
    500: "Erro interno do servidor.",
}


class ApiJsonErrorsMiddleware:
    """Converte qualquer erro HTML (404, 405, 500...) sob /api/ em JSON."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (request.path.startswith("/api/") and response.status_code >= 400
                and "application/json" not in response.get("Content-Type", "")):
            nova = JsonResponse(
                {"sucesso": False,
                 "mensagem": MENSAGENS.get(response.status_code, "Erro na requisição."),
                 "codigo": CODIGOS.get(response.status_code, "erro"),
                 "erros": {}},
                status=response.status_code,
            )
            if response.has_header("Allow"):
                nova["Allow"] = response["Allow"]
            return nova
        return response