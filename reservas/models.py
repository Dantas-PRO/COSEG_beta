from datetime import datetime

from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import models
from django.utils import timezone

from .services import (
    CAPACIDADES,
    MAX_PASSAGEIROS,
    descrever_conflito,
    reservas_conflitantes,
    travar_veiculos,
    validar_periodo,
)


class Veiculo(models.Model):
    class Categoria(models.TextChoices):
        LEVE = "LEVE", "Leve (até 4 passageiros)"
        COLETIVO = "COLETIVO", "Coletivo (até 18 passageiros)"

    codigo = models.CharField(max_length=10, unique=True)
    categoria = models.CharField(max_length=10, choices=Categoria.choices)
    capacidade = models.PositiveSmallIntegerField(editable=False)
    ativo = models.BooleanField(default=True)

    def clean(self):
        # Não deixa reduzir a capacidade abaixo do que já foi reservado
        if not self.pk or self.categoria not in CAPACIDADES:
            return
        nova = CAPACIDADES[self.categoria]
        afetadas = list(self.reservas.filter(
            status=Reserva.Status.ATIVA,
            data__gte=timezone.localdate(),
            passageiros__gt=nova,
        ))
        if afetadas:
            lista = ", ".join(
                f"#{r.pk} ({r.passageiros} passageiros em {r.data:%d/%m/%Y})" for r in afetadas
            )
            raise ValidationError({"categoria": ValidationError(
                f"Não é possível mudar para {self.categoria} (capacidade {nova}): há reservas "
                f"futuras com mais passageiros: {lista}. Cancele ou realoque antes.",
                code="reservas_incompativeis",
            )})

    def save(self, *args, **kwargs):
        # a capacidade é derivada da categoria (regra de negócio no model)
        self.capacidade = CAPACIDADES[self.categoria]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.codigo} ({self.capacidade} lugares)"


class Reserva(models.Model):
    class Status(models.TextChoices):
        ATIVA = "ATIVA", "Ativa"
        CANCELADA = "CANCELADA", "Cancelada"

    solicitante = models.CharField(max_length=120)
    setor = models.CharField(max_length=80)
    atividade = models.CharField(max_length=120)
    origem = models.CharField(max_length=120)
    destino = models.CharField(max_length=120)
    data = models.DateField()
    hora_saida = models.TimeField()
    # vazio = retorno no mesmo dia; preencher só em viagem que passa da meia-noite
    data_retorno = models.DateField(null=True, blank=True)
    hora_retorno = models.TimeField()
    passageiros = models.PositiveSmallIntegerField()
    veiculo = models.ForeignKey(Veiculo, on_delete=models.PROTECT, related_name="reservas")
    categoria_pretendida = models.CharField(
        max_length=10, choices=Veiculo.Categoria.choices, blank=True
    )
    observacoes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ATIVA)
    criado_em = models.DateTimeField(auto_now_add=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="reservas",
    )

    class Meta:
        ordering = ["data", "hora_saida"]

    # ----- período -----
    @property
    def data_fim(self):
        return self.data_retorno or self.data

    @property
    def inicio(self):
        return datetime.combine(self.data, self.hora_saida)

    @property
    def fim(self):
        return datetime.combine(self.data_fim, self.hora_retorno)

    def cancelar(self):
        if self.status == self.Status.CANCELADA:
            raise ValidationError("Esta reserva já está cancelada.", code="ja_cancelada")
        self.status = self.Status.CANCELADA
        self.save(update_fields=["status"])

    # ----- regras de negócio (valem para API E admin) -----
    def clean(self):
        erros = {}

        def add(campo, codigo, msg):
            erros.setdefault(campo, []).append(ValidationError(msg, code=codigo))

        original = Reserva.objects.filter(pk=self.pk).first() if self.pk else None

        if (original and original.status == self.Status.CANCELADA
                and self.status == self.Status.CANCELADA):
            raise ValidationError("Reserva cancelada não pode ser editada.", code="reserva_cancelada")

        v = self.veiculo if self.veiculo_id else None
        cat = self.categoria_pretendida

        agenda_mudou = original is None or (
            original.veiculo_id, original.data, original.hora_saida,
            original.hora_retorno, original.data_fim,
        ) != (self.veiculo_id, self.data, self.hora_saida, self.hora_retorno, self.data_fim)
        saida_mudou = original is None or (
            (original.data, original.hora_saida) != (self.data, self.hora_saida)
        )

        # 1) data e horários (passado só é checado se a saída foi criada/alterada)
        for campo, codigo, msg in validar_periodo(
            self.data, self.hora_saida, self.hora_retorno, self.data_retorno,
            checar_passado=saida_mudou,
        ):
            add(campo, codigo, msg)

        # 2) capacidade e categoria
        pax = self.passageiros
        if pax is not None:
            if pax < 1:
                add("passageiros", "passageiros_invalido", "Informe ao menos 1 passageiro.")
            elif pax > MAX_PASSAGEIROS:
                add("passageiros", "acima_do_limite",
                    f"Solicitação rejeitada: o máximo é {MAX_PASSAGEIROS} passageiros.")
            else:
                if v is not None and pax > v.capacidade:
                    add("passageiros", "capacidade_excedida",
                        f"O veículo {v.codigo} comporta {v.capacidade} passageiros "
                        f"e foram solicitados {pax}.")
                if v is None and cat and pax > CAPACIDADES[cat]:
                    add("passageiros", "capacidade_excedida",
                        f"A categoria {cat} comporta {CAPACIDADES[cat]} passageiros. "
                        "Escolha a categoria COLETIVO.")
        if v is not None and cat and v.categoria != cat:
            add("categoria_pretendida", "categoria_incompativel",
                f"O veículo {v.codigo} é da categoria {v.categoria}, "
                f"mas a categoria pretendida é {cat}.")

        # 3) veículo inativo (só bloqueia criar ou mudar a agenda; editar observações pode)
        if v is not None and not v.ativo and agenda_mudou:
            add("veiculo", "veiculo_inativo",
                f"O veículo {v.codigo} está inativo e não pode receber reservas.")

        # 4) conflito de horários (consulta ao banco, com o veículo travado)
        reativando = (original is not None and original.status != self.Status.ATIVA
                      and self.status == self.Status.ATIVA)
        periodo_ok = "hora_retorno" not in erros and "data_retorno" not in erros
        if (v is not None and self.data and self.hora_saida and self.hora_retorno
                and periodo_ok and self.status == self.Status.ATIVA
                and (agenda_mudou or reativando)):
            travar_veiculos([v.pk])
            conflitos = reservas_conflitantes(
                v, self.data, self.hora_saida, self.hora_retorno, self.data_retorno, self.pk
            )
            if conflitos:
                detalhes = "; ".join(descrever_conflito(c) for c in conflitos)
                add("veiculo", "conflito_horario",
                    f"Conflito: {v.codigo} já está reservado nesse intervalo ({detalhes}).")

        if erros:
            raise ValidationError(erros)

    def __str__(self):
        return f"{self.veiculo.codigo} - {self.data} {self.hora_saida}-{self.hora_retorno}"