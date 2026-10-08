import hashlib
import json

from django.contrib.auth import authenticate, login, logout
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import ProtectedError
from django.forms import DateField, IntegerField, TimeField
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from .forms import ReservaForm, VeiculoForm
from .models import Reserva, Veiculo
from .seguranca import login_obrigatorio
from .services import CAPACIDADES, MAX_PASSAGEIROS, avisos_reserva, validar_periodo, veiculos_disponiveis

MAX_TENTATIVAS = 5
BLOQUEIO_SEGUNDOS = 300
CODIGOS_CONFLITO = {"conflito_horario", "sem_veiculo_disponivel"}

TEXTOS_RESERVA = ("solicitante", "setor", "atividade", "origem", "destino", "observacoes",
                  "categoria_pretendida", "data", "hora_saida", "data_retorno", "hora_retorno")
INTEIROS_RESERVA = ("passageiros", "veiculo")
TEXTOS_VEICULO = ("codigo", "categoria")
BOOLEANOS_VEICULO = ("ativo",)
LIMITE_INT = 2 ** 31


# ---------- utilitários ----------
def _json(request):
    try:
        dados = json.loads(request.body or "{}")
    except (ValueError, RecursionError):
        return None
    return dados if isinstance(dados, dict) else None


def _erro(msg, status=400, erros=None, codigo=None):
    corpo = {"sucesso": False, "mensagem": msg, "erros": erros or {}}
    if codigo:
        corpo["codigo"] = codigo
    return JsonResponse(corpo, status=status)


def _tipos_invalidos(dados, textos=(), inteiros=(), booleanos=()):
    """Recusa lista, objeto, número ou booleano onde se espera outro tipo."""
    erros = {}

    def marcar(campo, esperado, codigo="tipo_invalido"):
        erros[campo] = [{"message": f"Tipo inválido para '{campo}': envie {esperado}.",
                         "code": codigo}]

    for c in textos:
        v = dados.get(c)
        if v is not None and not isinstance(v, str):
            marcar(c, "um texto")
    for c in inteiros:
        v = dados.get(c)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, str)):
            marcar(c, "um número inteiro")
        elif isinstance(v, int) and not (-LIMITE_INT < v < LIMITE_INT):
            marcar(c, "um número inteiro dentro do limite", "valor_invalido")
    for c in booleanos:
        v = dados.get(c)
        if v is not None and not isinstance(v, (bool, str)):
            marcar(c, "verdadeiro ou falso")
    return erros


def _negar_tipos(erros):
    if erros:
        return _erro("Tipos de dados inválidos.", erros=erros)
    return None


def _erros_de_validacao(exc):
    """ValidationError do Django -> mesmo formato do form.errors.get_json_data()."""
    def item(e):
        return {"message": e.messages[0], "code": e.code or "invalido"}

    if hasattr(exc, "error_dict"):
        return {campo: [item(e) for e in lista] for campo, lista in exc.error_dict.items()}
    return {"__all__": [item(e) for e in exc.error_list]}


def _resposta_de_erros(mensagem, erros):
    """400 para erro de validação; 409 quando há conflito de horário/disponibilidade."""
    codigos = {e["code"] for lista in erros.values() for e in lista}
    status = 409 if codigos & CODIGOS_CONFLITO else 400
    return _erro(mensagem, status=status, erros=erros)


def _resposta_invalida(mensagem, form):
    return _resposta_de_erros(mensagem, form.errors.get_json_data())


def _veiculo(v):
    return {"id": v.id, "codigo": v.codigo, "categoria": v.categoria,
            "capacidade": v.capacidade, "ativo": v.ativo}


def _reserva(r):
    return {
        "id": r.id, "solicitante": r.solicitante, "setor": r.setor,
        "atividade": r.atividade, "origem": r.origem, "destino": r.destino,
        "data": r.data.isoformat(), "hora_saida": r.hora_saida.strftime("%H:%M"),
        "data_retorno": r.data_fim.isoformat(),
        "hora_retorno": r.hora_retorno.strftime("%H:%M"),
        "passageiros": r.passageiros, "veiculo": _veiculo(r.veiculo),
        "categoria_pretendida": r.categoria_pretendida,
        "observacoes": r.observacoes, "status": r.status,
        "criado_por": r.criado_por.username if r.criado_por else None,
    }


def _mesclar_reserva(r, dados):
    """PUT/PATCH: o que não foi enviado continua como está (não troca veículo sozinho)."""
    atual = {
        "solicitante": r.solicitante, "setor": r.setor, "atividade": r.atividade,
        "origem": r.origem, "destino": r.destino, "data": r.data.isoformat(),
        "hora_saida": r.hora_saida.strftime("%H:%M:%S"),
        "hora_retorno": r.hora_retorno.strftime("%H:%M:%S"),
        "data_retorno": r.data_retorno.isoformat() if r.data_retorno else "",
        "passageiros": r.passageiros, "veiculo": r.veiculo_id,
        "categoria_pretendida": r.categoria_pretendida, "observacoes": r.observacoes,
    }
    if "veiculo" in dados and "categoria_pretendida" not in dados:
        atual["categoria_pretendida"] = ""  # categoria antiga não vale para o veículo novo
    atual.update(dados)
    return atual


def _mesclar_veiculo(v, dados):
    atual = {"codigo": v.codigo, "categoria": v.categoria, "ativo": v.ativo}
    atual.update(dados)
    return atual


def _negar_se_nao_coseg(request):
    if not request.user.is_staff:
        return _erro("Acesso restrito ao COSEG.", status=403, codigo="sem_permissao")
    return None


def _negar_se_alheia(request, reserva):
    if request.user.is_staff or reserva.criado_por_id == request.user.id:
        return None
    return _erro("Você só pode acessar as suas próprias reservas.", status=403, codigo="sem_permissao")


# ---------- AUTENTICAÇÃO ----------
def csrf_falhou(request, reason=""):
    return _erro("Falha de CSRF: envie o cabeçalho X-CSRFToken (obtenha em /api/csrf/).",
                 status=403, codigo="csrf")


@ensure_csrf_cookie
@require_http_methods(["GET"])
def csrf(request):
    return JsonResponse({"csrfToken": get_token(request)})


@require_http_methods(["POST"])
def login_view(request):
    dados = _json(request)
    if dados is None:
        return _erro("JSON inválido.")
    username = str(dados.get("username", "")).strip()
    senha = str(dados.get("password", ""))

    hash_user = hashlib.sha256(username.lower().encode()).hexdigest()[:16]
    chave = f"login:{request.META.get('REMOTE_ADDR', '')}:{hash_user}"
    if cache.get(chave, 0) >= MAX_TENTATIVAS:
        return _erro("Muitas tentativas de login. Aguarde alguns minutos.", status=429)

    user = authenticate(request, username=username, password=senha)
    if user is None:
        cache.set(chave, cache.get(chave, 0) + 1, BLOQUEIO_SEGUNDOS)
        return _erro("Usuário ou senha inválidos.", status=401)

    cache.delete(chave)
    login(request, user)
    return JsonResponse({"sucesso": True, "mensagem": "Login realizado.",
                         "usuario": {"username": user.username, "coseg": user.is_staff}})


@require_http_methods(["POST"])
def logout_view(request):
    logout(request)
    return JsonResponse({"sucesso": True, "mensagem": "Sessão encerrada."})


@login_obrigatorio
@require_http_methods(["GET"])
def eu(request):
    return JsonResponse({"username": request.user.username, "coseg": request.user.is_staff})


# ---------- VEÍCULOS ----------
@login_obrigatorio
@require_http_methods(["GET", "POST"])
def veiculos_lista(request):
    if request.method == "GET":
        return JsonResponse({"veiculos": [_veiculo(v) for v in Veiculo.objects.all()]})

    negado = _negar_se_nao_coseg(request)
    if negado is not None:
        return negado
    dados = _json(request)
    if dados is None:
        return _erro("JSON inválido.")
    negado = _negar_tipos(_tipos_invalidos(dados, TEXTOS_VEICULO, booleanos=BOOLEANOS_VEICULO))
    if negado is not None:
        return negado
    dados.setdefault("ativo", True)
    try:
        form = VeiculoForm(dados)
        if not form.is_valid():
            return _resposta_invalida("Dados inválidos.", form)
        v = form.save()
    except ValidationError as exc:
        return _resposta_de_erros("Dados inválidos.", _erros_de_validacao(exc))
    return JsonResponse({"sucesso": True, "mensagem": "Veículo cadastrado.", "veiculo": _veiculo(v)}, status=201)


@login_obrigatorio
@require_http_methods(["GET", "PUT", "PATCH", "DELETE"])
def veiculo_detalhe(request, pk):
    v = get_object_or_404(Veiculo, pk=pk)
    if request.method == "GET":
        return JsonResponse(_veiculo(v))

    negado = _negar_se_nao_coseg(request)
    if negado is not None:
        return negado
    if request.method in ("PUT", "PATCH"):
        dados = _json(request)
        if dados is None:
            return _erro("JSON inválido.")
        negado = _negar_tipos(_tipos_invalidos(dados, TEXTOS_VEICULO, booleanos=BOOLEANOS_VEICULO))
        if negado is not None:
            return negado
        try:
            form = VeiculoForm(_mesclar_veiculo(v, dados), instance=v)
            if not form.is_valid():
                return _resposta_invalida("Dados inválidos.", form)
            v = form.save()
        except ValidationError as exc:
            return _resposta_de_erros("Dados inválidos.", _erros_de_validacao(exc))
        return JsonResponse({"sucesso": True, "mensagem": "Veículo atualizado.", "veiculo": _veiculo(v)})
    try:
        v.delete()
    except ProtectedError:
        return _erro("Veículo possui reservas; desative-o em vez de excluir.", status=409, codigo="veiculo_com_reservas")
    return JsonResponse({"sucesso": True, "mensagem": "Veículo removido."})


# ---------- RESERVAS ----------
@login_obrigatorio
@require_http_methods(["GET", "POST"])
def reservas_lista(request):
    if request.method == "GET":
        qs = Reserva.objects.select_related("veiculo", "criado_por")
        if not request.user.is_staff:
            qs = qs.filter(criado_por=request.user)
        if request.GET.get("data"):
            try:
                qs = qs.filter(data=DateField().clean(request.GET["data"]))
            except ValidationError:
                return _erro("Data inválida. Use o formato AAAA-MM-DD.", codigo="data_invalida")
        if request.GET.get("veiculo"):
            qs = qs.filter(veiculo__codigo=request.GET["veiculo"])
        return JsonResponse({"reservas": [_reserva(r) for r in qs]})

    dados = _json(request)
    if dados is None:
        return _erro("JSON inválido.")
    negado = _negar_tipos(_tipos_invalidos(dados, TEXTOS_RESERVA, INTEIROS_RESERVA))
    if negado is not None:
        return negado
    # validação + gravação na MESMA transação (evita duas reservas simultâneas no mesmo horário)
    try:
        with transaction.atomic():
            form = ReservaForm(dados)
            if not form.is_valid():
                return _resposta_invalida("Não foi possível registrar a reserva.", form)
            r = form.save(commit=False)
            r.criado_por = request.user
            r.save()
    except ValidationError as exc:
        return _resposta_de_erros("Não foi possível registrar a reserva.", _erros_de_validacao(exc))
    return JsonResponse({"sucesso": True, "mensagem": "Reserva registrada com sucesso.",
                         "reserva": _reserva(r), "avisos": avisos_reserva(r)}, status=201)


@login_obrigatorio
@require_http_methods(["GET", "PUT", "PATCH", "DELETE"])
def reserva_detalhe(request, pk):
    r = get_object_or_404(Reserva.objects.select_related("veiculo", "criado_por"), pk=pk)
    negado = _negar_se_alheia(request, r)
    if negado is not None:
        return negado

    if request.method == "GET":
        return JsonResponse(_reserva(r))

    if request.method in ("PUT", "PATCH"):
        if r.status == Reserva.Status.CANCELADA:
            return _erro("Reserva cancelada não pode ser editada.", status=409, codigo="reserva_cancelada")
        dados = _json(request)
        if dados is None:
            return _erro("JSON inválido.")
        negado = _negar_tipos(_tipos_invalidos(dados, TEXTOS_RESERVA, INTEIROS_RESERVA))
        if negado is not None:
            return negado
        try:
            with transaction.atomic():
                form = ReservaForm(_mesclar_reserva(r, dados), instance=r)
                if not form.is_valid():
                    return _resposta_invalida("Não foi possível atualizar a reserva.", form)
                r = form.save()
        except ValidationError as exc:
            return _resposta_de_erros("Não foi possível atualizar a reserva.", _erros_de_validacao(exc))
        return JsonResponse({"sucesso": True, "mensagem": "Reserva atualizada.",
                             "reserva": _reserva(r), "avisos": avisos_reserva(r)})

    if not request.user.is_staff:
        return _erro("Somente o COSEG exclui reservas. Use /cancelar/.", status=403, codigo="sem_permissao")
    r.delete()
    return JsonResponse({"sucesso": True, "mensagem": "Reserva excluída."})


@login_obrigatorio
@require_http_methods(["POST"])
def reserva_cancelar(request, pk):
    r = get_object_or_404(Reserva, pk=pk)
    negado = _negar_se_alheia(request, r)
    if negado is not None:
        return negado
    try:
        r.cancelar()
    except ValidationError:
        return _erro("Esta reserva já está cancelada.", status=409, codigo="ja_cancelada")
    return JsonResponse({"sucesso": True, "mensagem": "Reserva cancelada; veículo liberado."})


# ---------- DISPONIBILIDADE ----------
@login_obrigatorio
@require_http_methods(["GET"])
def disponibilidade(request):
    q = request.GET
    campos = {
        "data": DateField(),
        "saida": TimeField(),
        "retorno": TimeField(),
        "data_retorno": DateField(required=False),
        "passageiros": IntegerField(min_value=1, max_value=MAX_PASSAGEIROS),
    }
    valores, erros = {}, {}
    for nome, campo in campos.items():
        try:
            valores[nome] = campo.clean(q.get(nome))
        except ValidationError as e:
            erros[nome] = [{"message": err.messages[0], "code": err.code or "invalido"}
                           for err in e.error_list]

    categoria = (q.get("categoria") or "").strip().upper() or None
    if categoria and categoria not in CAPACIDADES:
        erros["categoria"] = [{"message": "Categoria inexistente. Use LEVE ou COLETIVO.",
                               "code": "categoria_invalida"}]

    if not any(k in erros for k in ("data", "saida", "retorno", "data_retorno")):
        for campo, codigo, msg in validar_periodo(
            valores["data"], valores["saida"], valores["retorno"], valores["data_retorno"]
        ):
            nome = {"hora_saida": "saida", "hora_retorno": "retorno"}.get(campo, campo)
            erros.setdefault(nome, []).append({"message": msg, "code": codigo})

    if erros:
        return _erro("Parâmetros inválidos.", erros=erros)

    qs = veiculos_disponiveis(valores["data"], valores["saida"], valores["retorno"],
                              valores["passageiros"], categoria, valores["data_retorno"])
    return JsonResponse({"disponiveis": [_veiculo(v) for v in qs]})