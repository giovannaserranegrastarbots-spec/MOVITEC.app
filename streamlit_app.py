"""
Telemetria Educacional - Versão DEMO (dados simulados)
---------------------------------------------------------
Essa versão NÃO precisa de Arduino, sensor, porta serial nem Firebase.
Ela gera dados falsos de aceleração/frenagem, sozinha, só para você
construir e testar a interface, os gráficos, as métricas e o feedback
por voz.

Quando o robô estiver disponível de novo, essa mesma lógica de
interface pode ser reaproveitada trocando só a função que gera os
dados (gerar_leitura_simulada) pela leitura real da serial.

Como executar:
    pip install streamlit pandas
    streamlit run telemetria_demo_app.py
"""

import random
import time
from datetime import datetime

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Telemetria Educacional - Robô (Demo)", layout="wide")


# ----------------------------------------------------------------------
# Estado da sessão
# ----------------------------------------------------------------------
def inicializar_estado():
    defaults = {
        "coletando": False,
        "historico": pd.DataFrame(columns=["tempo", "accel_x", "forca_g"]),
        "total_aceleracoes": 0,
        "total_frenagens": 0,
        "estado_evento_anterior": "neutro",
        "sim_tipo_ativo": None,   # None | "acel" | "freio"
        "sim_ciclos_restantes": 0,
        "percurso_finalizado": False,
        "ultimo_resumo": "",
    }
    for chave, valor in defaults.items():
        if chave not in st.session_state:
            st.session_state[chave] = valor


inicializar_estado()


# ----------------------------------------------------------------------
# Simulação de dados (substitui o Arduino por enquanto)
# ----------------------------------------------------------------------
def gerar_leitura_simulada():
    """Gera um valor de aceleração longitudinal parecido com um robô real:
    a maior parte do tempo é ruído pequeno, com "rajadas" ocasionais de
    aceleração ou frenagem brusca, como se fosse um trajeto de verdade."""

    # 8% de chance de começar uma nova rajada de evento, se não houver uma ativa
    if st.session_state.sim_tipo_ativo is None and random.random() < 0.08:
        st.session_state.sim_tipo_ativo = random.choice(["acel", "freio"])
        st.session_state.sim_ciclos_restantes = random.randint(2, 5)

    if st.session_state.sim_tipo_ativo is None:
        accel_x = random.uniform(-0.5, 0.5)
    else:
        pico = random.uniform(3.0, 6.0)
        accel_x = pico if st.session_state.sim_tipo_ativo == "acel" else -pico
        st.session_state.sim_ciclos_restantes -= 1
        if st.session_state.sim_ciclos_restantes <= 0:
            st.session_state.sim_tipo_ativo = None

    forca_g = 1.0 + abs(accel_x) / 9.80665 + random.uniform(-0.03, 0.03)
    return round(accel_x, 2), round(forca_g, 2)


# ----------------------------------------------------------------------
# Detecção de eventos (mesma lógica que será usada com dados reais)
# ----------------------------------------------------------------------
def registrar_evento(accel_x, limiar_aceleracao, limiar_frenagem, margem_histerese):
    estado_anterior = st.session_state.estado_evento_anterior

    if accel_x >= limiar_aceleracao:
        if estado_anterior != "acelerando":
            st.session_state.total_aceleracoes += 1
        st.session_state.estado_evento_anterior = "acelerando"
    elif accel_x <= -limiar_frenagem:
        if estado_anterior != "freando":
            st.session_state.total_frenagens += 1
        st.session_state.estado_evento_anterior = "freando"
    elif abs(accel_x) <= margem_histerese:
        st.session_state.estado_evento_anterior = "neutro"


def gerar_resumo_texto():
    total_acel = st.session_state.total_aceleracoes
    total_fren = st.session_state.total_frenagens
    total_eventos = total_acel + total_fren

    if total_eventos == 0:
        return (
            "Percurso finalizado. Nenhum evento de condução brusca foi registrado. "
            "Excelente condução, dentro dos padrões de segurança."
        )
    if total_eventos <= 3:
        avaliacao = "A condução foi, no geral, tranquila, com poucos eventos."
    elif total_eventos <= 8:
        avaliacao = "A condução apresentou um número moderado de eventos bruscos. Atenção redobrada é recomendada."
    else:
        avaliacao = "A condução apresentou muitos eventos bruscos. É recomendado revisar o comportamento ao volante."

    return (
        f"Percurso finalizado. Foram registradas {int(total_acel)} acelerações indevidas e "
        f"{int(total_fren)} frenagens bruscas. {avaliacao}"
    )


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


# ----------------------------------------------------------------------
# Barra lateral
# ----------------------------------------------------------------------
st.sidebar.header("⚙️ Configurações")
st.sidebar.warning("🧪 Modo demonstração: os dados são simulados, não vêm de um Arduino real.")

limiar_aceleracao = st.sidebar.slider("Limiar de aceleração brusca (m/s²)", 0.5, 8.0, 2.5, 0.1)
limiar_frenagem = st.sidebar.slider("Limiar de frenagem brusca (m/s²)", 0.5, 8.0, 2.5, 0.1)
margem_histerese = st.sidebar.slider(
    "Margem para 'zerar' o evento (m/s²)", 0.1, 3.0, 1.0, 0.1,
    help="Depois de um evento, a leitura precisa voltar abaixo desta margem antes de contar um novo evento do mesmo tipo.",
)


# ----------------------------------------------------------------------
# Cabeçalho
# ----------------------------------------------------------------------
st.title("🤖 Telemetria Educacional - Comportamento do Robô")
st.caption("Monitoramento de acelerações e frenagens bruscas — versão demo com dados simulados")

st.divider()


# ----------------------------------------------------------------------
# Controles de percurso
# ----------------------------------------------------------------------
col_a, col_b, col_c = st.columns([1, 1, 2])

with col_a:
    if st.button("▶️ Iniciar Percurso", disabled=st.session_state.coletando, use_container_width=True):
        st.session_state.coletando = True
        st.session_state.percurso_finalizado = False
        st.session_state.historico = pd.DataFrame(columns=["tempo", "accel_x", "forca_g"])
        st.session_state.total_aceleracoes = 0
        st.session_state.total_frenagens = 0
        st.session_state.estado_evento_anterior = "neutro"
        st.session_state.sim_tipo_ativo = None
        st.rerun()

with col_b:
    if st.button(
        "⏹️ Finalizar Percurso",
        disabled=not st.session_state.coletando,
        use_container_width=True,
        type="primary",
    ):
        st.session_state.coletando = False
        st.session_state.percurso_finalizado = True
        st.session_state.ultimo_resumo = gerar_resumo_texto()
        st.rerun()

with col_c:
    if st.session_state.coletando:
        st.info("🟢 Simulando percurso em tempo real...")
    else:
        st.info("🟡 Pressione 'Iniciar Percurso' para começar a simulação.")

st.divider()


# ----------------------------------------------------------------------
# Métricas
# ----------------------------------------------------------------------
col_m1, col_m2, col_m3 = st.columns(3)
col_m1.metric("🚨 Frenagens Bruscas", int(st.session_state.total_frenagens))
col_m2.metric("⚠️ Acelerações Indevidas", int(st.session_state.total_aceleracoes))
col_m3.metric("📊 Total de Eventos", int(st.session_state.total_aceleracoes + st.session_state.total_frenagens))

st.divider()


# ----------------------------------------------------------------------
# Painel em tempo real (auto-atualiza a cada 0.5s)
# ----------------------------------------------------------------------
@st.fragment(run_every=0.5)
def painel_tempo_real():
    if st.session_state.coletando:
        accel_x, forca_g = gerar_leitura_simulada()
        registrar_evento(accel_x, limiar_aceleracao, limiar_frenagem, margem_histerese)

        nova_linha = pd.DataFrame(
            [{"tempo": datetime.now(), "accel_x": accel_x, "forca_g": forca_g}]
        )
        st.session_state.historico = pd.concat(
            [st.session_state.historico, nova_linha], ignore_index=True
        )

    historico = st.session_state.historico

    st.subheader("Gráficos em Tempo Real")
    if historico.empty:
        st.write("Aguardando início do percurso...")
    else:
        ultimos = historico.tail(200).set_index("tempo")
        aceleracao_positiva = ultimos["accel_x"].clip(lower=0)
        frenagem_positiva = (-ultimos["accel_x"]).clip(lower=0)

        col_g1, col_g2, col_g3 = st.columns(3)
        with col_g1:
            st.caption("Aceleração (m/s²)")
            st.line_chart(aceleracao_positiva)
        with col_g2:
            st.caption("Frenagem (m/s²)")
            st.line_chart(frenagem_positiva)
        with col_g3:
            st.caption("Força G total")
            st.line_chart(ultimos["forca_g"])

        with st.expander("Ver dados brutos (últimas 50 leituras)"):
            st.dataframe(historico.tail(50), use_container_width=True)


painel_tempo_real()


# ----------------------------------------------------------------------
# Resumo final e feedback por voz
# ----------------------------------------------------------------------
if st.session_state.percurso_finalizado:
    st.divider()
    st.subheader("📋 Resumo do Percurso")
    st.write(st.session_state.ultimo_resumo)

    if st.button("🔊 Ouvir Feedback"):
        falar_no_navegador(st.session_state.ultimo_resumo)
