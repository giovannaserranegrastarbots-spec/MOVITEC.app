"""
Telemetria Educacional - Versão NUVEM (dados reais via Bluetooth + Firebase)
-------------------------------------------------------------------------------
Atualizado com as regras de Limiares de Corte e Economia de Fichas (Gamificação).
"""

import json
import re
import time
from datetime import datetime

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Telemetria Educacional - Robô", layout="wide")


# ----------------------------------------------------------------------
# Estado da sessão
# ----------------------------------------------------------------------
def inicializar_estado():
    defaults = {
        "coletando": False,
        "historico": pd.DataFrame(columns=["tempo", "accel_x", "forca_g"]),
        "total_aceleracoes": 0,
        "total_frenagens": 0,
        "percurso_finalizado": False,
        "ultimo_resumo": "",
        "nota_ultima_corrida": None,
        "pontos_ultima_corrida": 0,
        "saldo_pontos_ano": 0,
        "historico_percursos": [],
        "resumo_pagina": 0,
        "mostrar_checklist": False,
        "mostrar_resumo": False,
        "nome_motorista": "",
        "ultimo_finalizado_processado": None,
        "amostras_ultima_corrida": 0,
        "duracao_ultima_corrida": 0.0,
        "horario_ultima_corrida": "",
    }
    for chave, valor in defaults.items():
        if chave not in st.session_state:
            st.session_state[chave] = valor


inicializar_estado()


# ----------------------------------------------------------------------
# Comunicação com a ponte local (via Firebase)
# ----------------------------------------------------------------------
FIREBASE_URL = "https://telemetria-app-281d2-default-rtdb.firebaseio.com/"


def firebase_configurado():
    return "SEU-PROJETO" not in FIREBASE_URL


def buscar_estado():
    if not firebase_configurado():
        return {}
    try:
        resposta = requests.get(f"{FIREBASE_URL}/estado.json", timeout=5)
        resposta.raise_for_status()
        return resposta.json() or {}
    except Exception:
        return {}


def enviar_comando(acao):
    if not firebase_configurado():
        return
    payload = {"acao": acao, "id": f"{acao}_{int(time.time() * 1000)}"}
    try:
        requests.put(f"{FIREBASE_URL}/comando.json", data=json.dumps(payload), timeout=5)
    except Exception:
        pass


def gerar_resumo_texto():
    total_acel = st.session_state.total_aceleracoes
    total_fren = st.session_state.total_frenagens
    total_eventos = total_acel + total_fren

    nota = st.session_state.nota_ultima_corrida
    pontos = st.session_state.pontos_ultima_corrida

    if nota >= 8.0:
        base = f"Percurso finalizado com excelência! Nota: {nota:.1f} de 10. Você ganhou +{int(pontos)} moedas de recompensa!"
    elif nota >= 5.0:
        base = f"Percurso finalizado com condução mediana. Nota: {nota:.1f} de 10. Você ganhou +{int(pontos)} moedas."
    else:
        base = f"Percurso finalizado com condução perigosa. Nota: {nota:.1f} de 10. Você ganhou 0 moedas."

    return base


# ----------------------------------------------------------------------
# Sistema de Gamificação (Economia de Fichas)
# ----------------------------------------------------------------------
def calcular_pontuacao(total_eventos):
    """
    Regra de Economia de Fichas:
    - Começa com Nota 10.0
    - Cada pico de evento desconta 1.5 pontos
    - Se nota >= 8.0 -> +10 moedas
    - Se nota >= 5.0 -> +5 moedas
    - Se nota < 5.0 -> 0 moedas
    """
    nota_qualidade = max(0.0, 10.0 - (total_eventos * 1.5))

    if nota_qualidade >= 8.0:
        moedas = 10
    elif nota_qualidade >= 5.0:
        moedas = 5
    else:
        moedas = 0

    return round(nota_qualidade, 1), int(moedas)


def falar_no_navegador(texto):
    texto_escapado = texto.replace('"', '\\"')
    components.html(
        f"""
        <script>
            const utter = new SpeechSynthesisUtterance("{texto_escapado}");
            utter.lang = "pt-BR";
            window.speechSynthesis.cancel();
            window.speechSynthesis.speak(utter);
        </script>
        """,
        height=0,
    )


ITENS_CHECKLIST = [
    "Cinto de segurança preso",
    "Faróis funcionando",
    "Nível de combustível/bateria verificado",
    "Calibragem dos pneus conferida",
    "Freios testados",
    "Área do percurso livre de obstáculos",
]


def chave_motorista(nome):
    chave = nome.strip().lower()
    chave = re.sub(r"[^a-z0-9_-]+", "_", chave)
    return chave or "motorista"


def carregar_dados_motorista(nome):
    if not firebase_configurado():
        return None
    try:
        chave = chave_motorista(nome)
        resposta = requests.get(f"{FIREBASE_URL}/motoristas/{chave}.json", timeout=5)
        resposta.raise_for_status()
        return resposta.json()
    except Exception:
        return None


def salvar_dados_motorista(nome, dados):
    if not firebase_configurado():
        return
    try:
        chave = chave_motorista(nome)
        requests.put(f"{FIREBASE_URL}/motoristas/{chave}.json", data=json.dumps(dados), timeout=5)
    except Exception:
        pass


def salvar_estado_atual():
    if not st.session_state.nome_motorista:
        return
    salvar_dados_motorista(
        st.session_state.nome_motorista,
        {
            "nome": st.session_state.nome_motorista,
            "historico_percursos": st.session_state.historico_percursos,
            "saldo_pontos_ano": st.session_state.saldo_pontos_ano,
        },
    )


def iniciar_percurso_de_verdade():
    enviar_comando("iniciar")
    st.session_state.coletando = True
    st.session_state.percurso_finalizado = False
    st.session_state.historico = pd.DataFrame(columns=["tempo", "accel_x", "forca_g"])
    st.session_state.total_aceleracoes = 0
    st.session_state.total_frenagens = 0


@st.dialog("✅ Checklist Pré-Corrida")
def checklist_dialog():
    nome_digitado = st.text_input("Nome do motorista", value=st.session_state.nome_motorista, key="input_nome_motorista")
    st.write("Confira todos os itens antes de iniciar a corrida:")
    marcados = [st.checkbox(item, key=f"check_{item}") for item in ITENS_CHECKLIST]

    todos_marcados = all(marcados)
    nome_valido = nome_digitado.strip() != ""

    if st.button("🚦 Confirmar e Iniciar Corrida", disabled=not (todos_marcados and nome_valido), type="primary"):
        for item in ITENS_CHECKLIST:
            st.session_state.pop(f"check_{item}", None)
        nome = nome_digitado.strip()
        st.session_state.nome_motorista = nome
        st.session_state.mostrar_checklist = False

        dados_salvos = carregar_dados_motorista(nome)
        if dados_salvos:
            st.session_state.historico_percursos = dados_salvos.get("historico_percursos", [])
            st.session_state.saldo_pontos_ano = dados_salvos.get("saldo_pontos_ano", 0)

        iniciar_percurso_de_verdade()
        st.rerun()


@st.dialog("📋 Resumo da Corrida")
def resumo_dialog():
    st.success("✅ Percurso concluído com sucesso!")
    st.write(st.session_state.ultimo_resumo)

    if st.button("🔊 Ouvir Feedback"):
        falar_no_navegador(st.session_state.ultimo_resumo)

    nota = st.session_state.nota_ultima_corrida
    pontos = st.session_state.pontos_ultima_corrida

    st.metric("⭐ Nota da Corrida", f"{nota:.1f}/10" if nota is not None else "—")
    st.metric("🪙 Moedas Ganhas", int(pontos))
    st.metric("💰 Saldo Total de Moedas", int(st.session_state.saldo_pontos_ano))

    if st.button("✖ Fechar"):
        st.session_state.mostrar_resumo = False
        st.rerun()


# ----------------------------------------------------------------------
# Layout e Controles
# ----------------------------------------------------------------------
st.title("🤖 Telemetria Educacional - Gamificação & Física")
st.caption("Monitoramento de acelerações e frenagens bruscas com MPU6050")

col_a, col_b, col_c = st.columns([1, 1, 2])

with col_a:
    if st.button("▶️ Iniciar Percurso", disabled=st.session_state.coletando, use_container_width=True):
        st.session_state.mostrar_resumo = False
        st.session_state.mostrar_checklist = True

with col_b:
    if st.button("⏹️ Finalizar Percurso", disabled=not st.session_state.coletando, use_container_width=True, type="primary"):
        enviar_comando("finalizar")
        st.session_state.coletando = False

with col_c:
    if st.session_state.coletando:
        st.info("🟢 Recebendo dados em tempo real do robô...")
    else:
        st.info("🟡 Pressione 'Iniciar Percurso' para começar.")

if st.session_state.mostrar_checklist:
    checklist_dialog()

if st.session_state.mostrar_resumo:
    resumo_dialog()

st.divider()

# --- MÉTRICAS ---
col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
col_m1.metric("🚨 Frenagens Bruscas", int(st.session_state.total_frenagens))
col_m2.metric("⚠️ Acelerações Indevidas", int(st.session_state.total_aceleracoes))
col_m3.metric("📊 Total de Eventos", int(st.session_state.total_aceleracoes + st.session_state.total_frenagens))
nota_exibida = st.session_state.nota_ultima_corrida
col_m4.metric("⭐ Nota da Última", f"{nota_exibida:.1f}/10" if nota_exibida is not None else "—")
col_m5.metric("🪙 Saldo de Moedas", int(st.session_state.saldo_pontos_ano))

st.divider()


# --- PAINEL EM TEMPO REAL ---
@st.fragment(run_every=2)
def painel_tempo_real():
    estado = buscar_estado()

    if estado:
        st.session_state.coletando = estado.get("coletando", False)
        st.session_state.total_aceleracoes = estado.get("total_aceleracoes", 0)
        st.session_state.total_frenagens = estado.get("total_frenagens", 0)

        amostras = estado.get("amostras", [])
        if amostras:
            st.session_state.historico = pd.DataFrame(amostras)

        marca_finalizacao = estado.get("finalizado_em")
        if estado.get("finalizado") and marca_finalizacao and marca_finalizacao != st.session_state.ultimo_finalizado_processado:
            duracao_min = estado.get("duracao_min", 0.01)
            total_eventos = st.session_state.total_aceleracoes + st.session_state.total_frenagens

            nota, pontos = calcular_pontuacao(total_eventos)
            st.session_state.nota_ultima_corrida = nota
            st.session_state.pontos_ultima_corrida = pontos
            st.session_state.saldo_pontos_ano += pontos
            st.session_state.amostras_ultima_corrida = len(amostras)
            st.session_state.duracao_ultima_corrida = round(duracao_min, 2)
            st.session_state.horario_ultima_corrida = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            
            st.session_state.historico_percursos.append(
                {
                    "Data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                    "Duração (min)": round(duracao_min, 1),
                    "Eventos": total_eventos,
                    "Nota": nota,
                    "Moedas ganhas": pontos,
                }
            )

            st.session_state.ultimo_resumo = gerar_resumo_texto()
            st.session_state.mostrar_resumo = True
            st.session_state.ultimo_finalizado_processado = marca_finalizacao
            st.session_state.percurso_finalizado = True

            salvar_estado_atual()
            st.rerun()

    historico = st.session_state.historico

    st.subheader("📊 Gráficos de Telemetria com Limiares de Calibração")
    if historico.empty:
        st.write("Aguardando início do percurso...")
    else:
        ultimos = historico.tail(200).set_index("tempo")
        
        st.caption("Faixas de Referência: 🟢 Seguro (-1.5 a 1.5 m/s²) | 🟡 Alerta (1.5 a 3.5 m/s²) | 🔴 Risco (> 3.5 m/s²)")
        
        col_g1, col_g2 = st.columns(2)
        with col_g1:
            st.markdown("**Aceleração Longitudinal (m/s²)**")
            st.line_chart(ultimos["accel_x"])
        with col_g2:
            st.markdown("**Força G Total**")
            st.line_chart(ultimos["forca_g"])


painel_tempo_real()

# --- EXTRATO DE PONTOS ---
st.divider()
st.subheader("📅 Extrato de Moedas e Histórico")

if not st.session_state.historico_percursos:
    st.write("Nenhuma corrida registrada ainda nesta sessão.")
else:
    df_extrato = pd.DataFrame(st.session_state.historico_percursos)
    st.dataframe(df_extrato, use_container_width=True, hide_index=True)
    st.metric("🪙 Saldo Acumulado de Moedas", int(st.session_state.saldo_pontos_ano))
