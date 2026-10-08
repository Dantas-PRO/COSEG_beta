import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from .models import Reserva, Veiculo


class ReservaAPITests(TestCase):
    def setUp(self):
        self.vl = Veiculo.objects.create(codigo="VL-01", categoria="LEVE")
        self.vc = Veiculo.objects.create(codigo="VC-01", categoria="COLETIVO")
        self.dia = timezone.localdate() + timedelta(days=30)
        self.coseg = get_user_model().objects.create_user(
            "coseg", password="Senha#Teste123", is_staff=True)
        self.client.force_login(self.coseg)

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


class SegurancaAPITests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.coseg = User.objects.create_user("coseg", password="Senha#Teste123", is_staff=True)
        self.colab = User.objects.create_user("colab", password="Senha#Teste123")
        self.vl = Veiculo.objects.create(codigo="VL-01", categoria="LEVE")
        self.dia = timezone.localdate() + timedelta(days=30)
        self.c_coseg, self.c_colab = Client(), Client()
        self.c_coseg.force_login(self.coseg)
        self.c_colab.force_login(self.colab)

    def payload(self, **extra):
        base = {"solicitante": "Ana", "setor": "TI", "atividade": "Reunião",
                "origem": "Itaqui", "destino": "Centro", "data": self.dia.isoformat(),
                "hora_saida": "08:00", "hora_retorno": "10:00",
                "passageiros": 3, "veiculo": self.vl.id}
        base.update(extra)
        return json.dumps(base)

    def login(self, client, user, senha):
        return client.post("/api/login/", json.dumps({"username": user, "password": senha}),
                           content_type="application/json")

    def test_sem_login_retorna_401(self):
        anonimo = Client()
        for url in ["/api/veiculos/", "/api/reservas/", "/api/disponibilidade/"]:
            self.assertEqual(anonimo.get(url).status_code, 401)

    def test_colaborador_nao_gerencia_frota(self):
        corpo = json.dumps({"codigo": "VL-99", "categoria": "LEVE", "ativo": True})
        self.assertEqual(self.c_colab.post("/api/veiculos/", corpo, content_type="application/json").status_code, 403)
        self.assertEqual(self.c_colab.delete(f"/api/veiculos/{self.vl.id}/").status_code, 403)

    def test_colaborador_so_ve_as_proprias_reservas(self):
        self.c_coseg.post("/api/reservas/", self.payload(), content_type="application/json")
        self.assertEqual(self.c_colab.get("/api/reservas/").json()["reservas"], [])
        r = self.c_colab.post("/api/reservas/",
                              self.payload(hora_saida="13:00", hora_retorno="15:00"),
                              content_type="application/json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(len(self.c_colab.get("/api/reservas/").json()["reservas"]), 1)
        self.assertEqual(len(self.c_coseg.get("/api/reservas/").json()["reservas"]), 2)

    def test_colaborador_nao_acessa_reserva_alheia(self):
        pk = self.c_coseg.post("/api/reservas/", self.payload(),
                               content_type="application/json").json()["reserva"]["id"]
        self.assertEqual(self.c_colab.get(f"/api/reservas/{pk}/").status_code, 403)
        self.assertEqual(self.c_colab.post(f"/api/reservas/{pk}/cancelar/").status_code, 403)

    def test_colaborador_nao_exclui_reserva(self):
        pk = self.c_colab.post("/api/reservas/", self.payload(),
                               content_type="application/json").json()["reserva"]["id"]
        self.assertEqual(self.c_colab.delete(f"/api/reservas/{pk}/").status_code, 403)
        self.assertEqual(self.c_colab.post(f"/api/reservas/{pk}/cancelar/").status_code, 200)

    def test_login_e_logout(self):
        c = Client()
        self.assertEqual(self.login(c, "coseg", "errada").status_code, 401)
        self.assertEqual(self.login(c, "coseg", "Senha#Teste123").status_code, 200)
        self.assertEqual(c.get("/api/eu/").status_code, 200)
        c.post("/api/logout/")
        self.assertEqual(c.get("/api/eu/").status_code, 401)

    def test_bloqueio_apos_muitas_tentativas(self):
        c = Client()
        for _ in range(5):
            self.login(c, "coseg", "errada")
        self.assertEqual(self.login(c, "coseg", "Senha#Teste123").status_code, 429)

    def test_csrf_exigido_nas_escritas(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.coseg)
        r = c.post("/api/reservas/", self.payload(), content_type="application/json")
        self.assertEqual(r.status_code, 403)
        self.assertFalse(r.json()["sucesso"])