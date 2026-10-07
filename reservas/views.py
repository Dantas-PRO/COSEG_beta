import json

from django.db.models import ProtectedError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .forms import ReservaForm, VeiculoForm
from .models import Reserva, Veiculo
from .services import veiculos_disponiveis
from django.forms import DateField, TimeField, IntegerField


def _json(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return None


def _erro(msg, status=400, erros=None):
    return JsonResponse({"sucesso": False, "mensagem": msg, "erros": erros or {}}, status=status)


def _veiculo(v):
    return {"id": v.id, "codigo": v.codigo, "categoria": v.categoria,
            "capacidade": v.capacidade, "ativo": v.ativo}


def _reserva(r):
    return {
        "id": r.id, "solicitante": r.solicitante, "setor": r.setor,
        "atividade": r.atividade, "origem": r.origem, "destino": r.destino,
        "data": r.data.isoformat(), "hora_saida": r.hora_saida.strftime("%H:%M"),
        "hora_retorno": r.hora_retorno.strftime("%H:%M"),
        "passageiros": r.passageiros, "veiculo": _veiculo(r.veiculo),
        "categoria_pretendida": r.categoria_pretendida,
        "observacoes": r.observacoes, "status": r.status,
    }


# ---------- VEÍCULOS ----------
@csrf_exempt
@require_http_methods(["GET", "POST"])
def veiculos_lista(request):
    if request.method == "GET":
        return JsonResponse({"veiculos": [_veiculo(v) for v in Veiculo.objects.all()]})
    dados = _json(request)
    if dados is None:
        return _erro("JSON inválido.")
    form = VeiculoForm(dados)
    if not form.is_valid():
        return _erro("Dados inválidos.", erros=form.errors.get_json_data())
    v = form.save()
    return JsonResponse({"sucesso": True, "mensagem": "Veículo cadastrado.", "veiculo": _veiculo(v)}, status=201)


@csrf_exempt
@require_http_methods(["GET", "PUT", "DELETE"])
def veiculo_detalhe(request, pk):
    v = get_object_or_404(Veiculo, pk=pk)
    if request.method == "GET":
        return JsonResponse(_veiculo(v))
    if request.method == "PUT":
        dados = _json(request)
        if dados is None:
            return _erro("JSON inválido.")
        form = VeiculoForm(dados, instance=v)
        if not form.is_valid():
            return _erro("Dados inválidos.", erros=form.errors.get_json_data())
        return JsonResponse({"sucesso": True, "mensagem": "Veículo atualizado.", "veiculo": _veiculo(form.save())})
    try:
        v.delete()
    except ProtectedError:
        return _erro("Veículo possui reservas; desative-o em vez de excluir.", status=409)
    return JsonResponse({"sucesso": True, "mensagem": "Veículo removido."})


# ---------- RESERVAS ----------
@csrf_exempt
@require_http_methods(["GET", "POST"])
def reservas_lista(request):
    if request.method == "GET":
        qs = Reserva.objects.select_related("veiculo")
        if request.GET.get("data"):
            qs = qs.filter(data=request.GET["data"])
        if request.GET.get("veiculo"):
            qs = qs.filter(veiculo__codigo=request.GET["veiculo"])
        return JsonResponse({"reservas": [_reserva(r) for r in qs]})

    dados = _json(request)
    if dados is None:
        return _erro("JSON inválido.")
    form = ReservaForm(dados)
    if not form.is_valid():
        return _erro("Não foi possível registrar a reserva.", erros=form.errors.get_json_data())
    r = form.save()
    return JsonResponse({"sucesso": True, "mensagem": "Reserva registrada com sucesso.", "reserva": _reserva(r)}, status=201)


@csrf_exempt
@require_http_methods(["GET", "PUT", "DELETE"])
def reserva_detalhe(request, pk):
    r = get_object_or_404(Reserva, pk=pk)
    if request.method == "GET":
        return JsonResponse(_reserva(r))
    if request.method == "PUT":
        dados = _json(request)
        if dados is None:
            return _erro("JSON inválido.")
        form = ReservaForm(dados, instance=r)
        if not form.is_valid():
            return _erro("Não foi possível atualizar a reserva.", erros=form.errors.get_json_data())
        return JsonResponse({"sucesso": True, "mensagem": "Reserva atualizada.", "reserva": _reserva(form.save())})
    r.delete()
    return JsonResponse({"sucesso": True, "mensagem": "Reserva excluída."})


@csrf_exempt
@require_http_methods(["POST"])
def reserva_cancelar(request, pk):
    r = get_object_or_404(Reserva, pk=pk)
    r.status = Reserva.Status.CANCELADA
    r.save()
    return JsonResponse({"sucesso": True, "mensagem": "Reserva cancelada; veículo liberado."})


# ---------- DISPONIBILIDADE (ajuda o front do PBL 2) ----------
@require_http_methods(["GET"])
def disponibilidade(request):
    try:
        data = DateField().clean(request.GET.get("data"))
        saida = TimeField().clean(request.GET.get("saida"))
        retorno = TimeField().clean(request.GET.get("retorno"))
        pax = IntegerField(min_value=1).clean(request.GET.get("passageiros"))
    except Exception:
        return _erro("Informe data, saida, retorno e passageiros válidos.")
    if retorno <= saida:
        return _erro("O horário de retorno deve ser posterior ao de saída.")
    qs = veiculos_disponiveis(data, saida, retorno, pax, request.GET.get("categoria") or None)
    return JsonResponse({"disponiveis": [_veiculo(v) for v in qs]})