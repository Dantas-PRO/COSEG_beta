from django.db import models
from django.conf import settings

CAPACIDADES = {"LEVE": 4, "COLETIVO": 18}


class Veiculo(models.Model):
    class Categoria(models.TextChoices):
        LEVE = "LEVE", "Leve (até 4 passageiros)"
        COLETIVO = "COLETIVO", "Coletivo (até 18 passageiros)"

    codigo = models.CharField(max_length=10, unique=True)
    categoria = models.CharField(max_length=10, choices=Categoria.choices)
    capacidade = models.PositiveSmallIntegerField(editable=False)
    ativo = models.BooleanField(default=True)

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

    def __str__(self):
        return f"{self.veiculo.codigo} - {self.data} {self.hora_saida}-{self.hora_retorno}"