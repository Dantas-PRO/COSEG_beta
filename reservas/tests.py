import json
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from .models import Reserva, Veiculo


class ReservaAPITests(TestCase):
    def setUp(self):
        self.vl = Veiculo.objects.create(codigo="VL-01", categoria="LEVE")
        self.vc = Veiculo.objects.create(codigo="VC-01", categoria="COLETIVO")
        self.dia = timezone.localdate() + timedelta(days=30)

    def payload(self, **extra):
        base = {
            "solicitante": "Ana", "setor": "TI", "atividade": "Reunião",
            "origem": "Itaqui", "destino": "Centro", "data": self.dia.isoformat(),
            "hora_saida": "08:00", "hora_retorno": "10:00",
            "passageiros": 3, "veiculo": self.vl.id,
        }
        base.update(extra)
        return base

    def post(self, dados):
        return self.client.post("/api/reservas/", json.dumps(dados), content_type="application/json")

    def test_persistencia_e_crud(self):
        r = self.post(self.payload())
        self.assertEqual(r.status_code, 201)
        pk = r.json()["reserva"]["id"]
        self.assertTrue(Reserva.objects.filter(pk=pk).exists())
        r = self.client.put(f"/api/reservas/{pk}/", json.dumps(self.payload(passageiros=2)), content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Reserva.objects.get(pk=pk).passageiros, 2)
        self.assertEqual(self.client.delete(f"/api/reservas/{pk}/").status_code, 200)
        self.assertFalse(Reserva.objects.filter(pk=pk).exists())

    def test_campos_obrigatorios(self):
        self.assertEqual(self.post({}).status_code, 400)

    def test_capacidade_leve(self):
        self.assertEqual(self.post(self.payload(passageiros=7)).status_code, 400)

    def test_acima_de_18(self):
        r = self.post(self.payload(passageiros=19, veiculo=self.vc.id))
        self.assertEqual(r.status_code, 400)

    def test_data_passada(self):
        ontem = (timezone.localdate() - timedelta(days=1)).isoformat()
        self.assertEqual(self.post(self.payload(data=ontem)).status_code, 400)

    def test_retorno_antes_da_saida(self):
        r = self.post(self.payload(hora_saida="10:00", hora_retorno="09:00"))
        self.assertEqual(r.status_code, 400)

    def test_conflito_sobreposicao(self):
        self.assertEqual(self.post(self.payload()).status_code, 201)
        r = self.post(self.payload(hora_saida="09:00", hora_retorno="11:00"))
        self.assertEqual(r.status_code, 400)

    def test_sem_conflito(self):
        self.post(self.payload())
        r = self.post(self.payload(hora_saida="10:30", hora_retorno="12:00"))
        self.assertEqual(r.status_code, 201)

    def test_atribuicao_automatica_por_categoria(self):
        dados = self.payload(passageiros=10, categoria_pretendida="COLETIVO")
        dados.pop("veiculo")
        r = self.post(dados)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["reserva"]["veiculo"]["codigo"], "VC-01")