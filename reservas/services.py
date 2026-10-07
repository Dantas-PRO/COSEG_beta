from .models import Reserva, Veiculo


def reservas_conflitantes(veiculo, data, saida, retorno, ignorar_pk=None):
    qs = Reserva.objects.filter(
        veiculo=veiculo,
        data=data,
        status=Reserva.Status.ATIVA,
        hora_saida__lt=retorno,
        hora_retorno__gt=saida,
    )
    if ignorar_pk:
        qs = qs.exclude(pk=ignorar_pk)
    return qs


def veiculos_disponiveis(data, saida, retorno, passageiros, categoria=None, ignorar_pk=None):
    ocupados = Reserva.objects.filter(
        data=data,
        status=Reserva.Status.ATIVA,
        hora_saida__lt=retorno,
        hora_retorno__gt=saida,
    )
    if ignorar_pk:
        ocupados = ocupados.exclude(pk=ignorar_pk)

    qs = Veiculo.objects.filter(ativo=True, capacidade__gte=passageiros)
    qs = qs.exclude(pk__in=ocupados.values("veiculo_id"))
    if categoria:
        qs = qs.filter(categoria=categoria)
    return qs.order_by("capacidade", "codigo")  # sugere o menor veículo que atende