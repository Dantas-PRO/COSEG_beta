from datetime import datetime

from django.conf import settings
from django.db import connection
from django.db.models.functions import Coalesce
from django.utils import timezone

CAPACIDADES = {"LEVE": 4, "COLETIVO": 18}
MAX_PASSAGEIROS = 18
MAX_DIAS_VIAGEM = 1  # o retorno pode ser, no máximo, 1 dia depois da saída


def permitir_data_passada():
    """Modo demonstração (variável COSEG_PERMITIR_DATA_PASSADA no .env)."""
    return getattr(settings, "COSEG_PERMITIR_DATA_PASSADA", False)


def intervalo(data, hora_saida, hora_retorno, data_retorno=None):
    """Início e fim da reserva como datetimes (permite viagem que passa da meia-noite)."""
    return (
        datetime.combine(data, hora_saida),
        datetime.combine(data_retorno or data, hora_retorno),
    )


def validar_periodo(data, hora_saida, hora_retorno, data_retorno=None, checar_passado=True):
    """Devolve uma lista de (campo, codigo, mensagem). Lista vazia = período válido."""
    erros = []
    if data is None:
        return erros

    if checar_passado and not permitir_data_passada():
        agora = timezone.localtime()
        if data < agora.date():
            erros.append(("data", "data_passada", "A data não pode estar no passado."))
        elif (data == agora.date() and hora_saida is not None
              and hora_saida < agora.time().replace(second=0, microsecond=0)):
            erros.append((
                "hora_saida", "horario_passado",
                f"O horário de saída ({hora_saida:%H:%M}) já passou hoje.",
            ))

    data_retorno_ok = True
    if data_retorno is not None:
        if data_retorno < data:
            data_retorno_ok = False
            erros.append(("data_retorno", "data_retorno_invalida",
                          "A data de retorno não pode ser anterior à data de saída."))
        elif (data_retorno - data).days > MAX_DIAS_VIAGEM:
            data_retorno_ok = False
            erros.append(("data_retorno", "data_retorno_invalida",
                          f"O retorno pode ocorrer, no máximo, {MAX_DIAS_VIAGEM} dia após a saída."))

    if data_retorno_ok and hora_saida is not None and hora_retorno is not None:
        inicio, fim = intervalo(data, hora_saida, hora_retorno, data_retorno)
        if fim <= inicio:
            msg = "O horário de retorno deve ser posterior ao de saída."
            if data_retorno is None:
                msg += " Para viagens que passam da meia-noite, informe também a data_retorno."
            erros.append(("hora_retorno", "retorno_invalido", msg))
    return erros


def reservas_ativas_no_periodo(data, data_fim, ignorar_pk=None):
    """Pré-filtro no banco: reservas ativas que tocam o intervalo de datas."""
    from .models import Reserva

    qs = (
        Reserva.objects.filter(status=Reserva.Status.ATIVA)
        .annotate(data_fim_efetiva=Coalesce("data_retorno", "data"))
        .filter(data__lte=data_fim, data_fim_efetiva__gte=data)
        .select_related("veiculo")
    )
    if ignorar_pk:
        qs = qs.exclude(pk=ignorar_pk)
    return qs


def reservas_conflitantes(veiculo, data, saida, retorno, data_retorno=None, ignorar_pk=None):
    """Conflito real: mesmo veículo e intervalos que se sobrepõem (início < fim alheio)."""
    inicio, fim = intervalo(data, saida, retorno, data_retorno)
    candidatas = reservas_ativas_no_periodo(data, data_retorno or data, ignorar_pk).filter(veiculo=veiculo)
    return [r for r in candidatas if r.inicio < fim and r.fim > inicio]


def descrever_conflito(r):
    return (f"reserva #{r.pk} de {r.veiculo.codigo} "
            f"({r.data:%d/%m/%Y} {r.hora_saida:%H:%M} até {r.data_fim:%d/%m/%Y} {r.hora_retorno:%H:%M})")


def veiculos_disponiveis(data, saida, retorno, passageiros, categoria=None,
                         data_retorno=None, ignorar_pk=None):
    from .models import Veiculo

    inicio, fim = intervalo(data, saida, retorno, data_retorno)
    ocupados = {
        r.veiculo_id
        for r in reservas_ativas_no_periodo(data, data_retorno or data, ignorar_pk)
        if r.inicio < fim and r.fim > inicio
    }
    qs = Veiculo.objects.filter(ativo=True, capacidade__gte=passageiros).exclude(pk__in=ocupados)
    if categoria:
        qs = qs.filter(categoria=categoria)
    return qs.order_by("capacidade", "codigo")  # sugere o menor veículo que atende


def travar_veiculos(ids=None):
    """Bloqueia as linhas dos veículos até o fim da transação (PostgreSQL/MySQL).
    No SQLite a serialização vem do transaction_mode=IMMEDIATE (settings)."""
    if not connection.in_atomic_block:
        return
    from .models import Veiculo

    qs = Veiculo.objects.select_for_update().order_by("pk")
    if ids is not None:
        qs = qs.filter(pk__in=ids)
    list(qs.values_list("pk", flat=True))


def avisos_reserva(reserva):
    """Avisos que não bloqueiam a reserva."""
    avisos = []
    v = reserva.veiculo
    if v.categoria == "COLETIVO" and reserva.passageiros <= CAPACIDADES["LEVE"]:
        avisos.append(
            f"O veículo {v.codigo} é coletivo ({v.capacidade} lugares) e a reserva tem apenas "
            f"{reserva.passageiros} passageiro(s). Um veículo leve atenderia."
        )
    return avisos