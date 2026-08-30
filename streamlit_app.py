"""
Telemetria Educacional - Painel Streamlit
------------------------------------------
Lê dados do Arduino (MPU6050) via porta serial no formato:
    aceleracao,frenagem\n

Exibe métricas e gráficos em tempo real e, ao finalizar o percurso,
gera um resumo falado (Text-to-Speech) com o feedback de condução.

Como executar:
    streamlit run telemetria_app.py
"""

import time
from datetime import datetime

import pandas as pd
import serial
import serial.tools.list_ports
import streamlit as st

try:
    import pyttsx3
    TTS_DISPONIVEL = True
except ImportError:
    TTS_DISPONIVEL = False


# ----------------------------------------------------------------------
# Configuração da página
# ----------------------------------------------------------------------
st.set_page_config(page_title="Telemetria Educacional - Robô", layout="wide")


# ----------------------------------------------------------------------
# Estado da sessão
# ----------------------------------------------------------------------
def inicializar_estado():
    defaults = {
        "conectado": False,
        "coletando": False,
        "serial_obj": None,
        "historico": pd.DataFrame(columns=["tempo", "aceleracao", "frenagem"]),
        "total_aceleracoes": 0,
        "total_frenagens": 0,
        "percurso_finalizado": False,
        "ultimo_resumo": "",
    }
    for chave, valor in defaults.items():
        if chave not in st.session_state:
            st.session_state[chave] = valor


inicializar_estado()


# ----------------------------------------------------------------------
# Funções auxiliares
# ----------------------------------------------------------------------
def listar_portas():
    portas = serial.tools.list_ports.comports()
    return [p.device for p in portas]


def conectar_serial(porta, baud):
    try:
        conexao = serial.Serial(porta, baud, timeout=1)
        time.sleep(2)  # tempo para o Arduino reiniciar após abrir a porta
        conexao.reset_input_buffer()
        return conexao
    except Exception as erro:
        st.error(f"Não foi possível conectar à porta {porta}: {erro}")
        return None


def desconectar_serial():
    if st.session_state.serial_obj is not None:
        try:
            st.session_state.serial_obj.close()
        except Exception:
            pass
    st.session_state.serial_obj = None
    st.session_state.conectado = False
    st.session_state.coletando = False


def ler_novas_linhas():
    """Lê todas as linhas disponíveis no buffer serial e atualiza o histórico."""
    conexao = st.session_state.serial_obj
    if conexao is None:
        return

    novas_linhas = []
    try:
        while conexao.in_waiting > 0:
            linha_bruta = conexao.readline().decode("utf-8", errors="ignore").strip()
            if not linha_bruta:
                continue
            partes = linha_bruta.split(",")
            if len(partes) != 2:
                continue
            try:
                aceleracao = float(partes[0])
                frenagem = float(partes[1])
            except ValueError:
                continue
            novas_linhas.append(
                {
                    "tempo": datetime.now(),
                    "aceleracao": aceleracao,
                    "frenagem": frenagem,
                }
            )
    except Exception as erro:
        st.warning(f"Erro na leitura serial: {erro}")
        desconectar_serial()
        return

    if novas_linhas:
        df_novo = pd.DataFrame(novas_linhas)
        st.session_state.historico = pd.concat(
            [st.session_state.historico, df_novo], ignore_index=True
        )
        st.session_state.total_aceleracoes += (df_novo["aceleracao"] > 0).sum()
        st.session_state.total_frenagens += (df_novo["frenagem"] > 0).sum()


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

    resumo = (
        f"Percurso finalizado. "
        f"Foram registradas {int(total_acel)} acelerações indevidas e "
        f"{int(total_fren)} frenagens bruscas. "
        f"{avaliacao}"
    )
    return resumo


def falar_texto(texto):
    if not TTS_DISPONIVEL:
        st.warning(
            "A biblioteca pyttsx3 não está instalada ou não encontrou um "
            "engine de voz no sistema. Instale com: pip install pyttsx3"
        )
        return
    try:
        engine = pyttsx3.init()
        engine.say(texto)
        engine.runAndWait()
        engine.stop()
    except Exception as erro:
        st.warning(f"Não foi possível reproduzir o áudio: {erro}")


# ----------------------------------------------------------------------
# Barra lateral - conexão
# ----------------------------------------------------------------------
st.sidebar.header("Conexão com o Arduino")

portas_disponiveis = listar_portas()
porta_selecionada = st.sidebar.selectbox(
    "Porta Serial", options=portas_disponiveis if portas_disponiveis else ["Nenhuma porta encontrada"]
)
baud_rate = st.sidebar.selectbox("Baud Rate", options=[115200, 9600, 57600], index=0)

col_conectar, col_desconectar = st.sidebar.columns(2)

with col_conectar:
    if st.button("Conectar", disabled=st.session_state.conectado):
        if portas_disponiveis:
            conexao = conectar_serial(porta_selecionada, baud_rate)
            if conexao is not None:
                st.session_state.serial_obj = conexao
                st.session_state.conectado = True
                st.success(f"Conectado em {porta_selecionada}")
        else:
            st.sidebar.error("Nenhuma porta serial disponível.")

with col_desconectar:
    if st.button("Desconectar", disabled=not st.session_state.conectado):
        desconectar_serial()
        st.rerun()

st.sidebar.divider()
st.sidebar.caption(
    "Certifique-se de que o Monitor Serial da IDE do Arduino esteja fechado, "
    "pois apenas um programa pode usar a porta por vez."
)


# ----------------------------------------------------------------------
# Cabeçalho
# ----------------------------------------------------------------------
st.title("🤖 Telemetria Educacional - Comportamento do Robô")
st.caption("Monitoramento em tempo real de acelerações e frenagens bruscas via MPU6050")

st.divider()


# ----------------------------------------------------------------------
# Controles de percurso
# ----------------------------------------------------------------------
col_a, col_b, col_c = st.columns([1, 1, 2])

with col_a:
    if st.button(
        "▶️ Iniciar Percurso",
        disabled=not st.session_state.conectado or st.session_state.coletando,
        use_container_width=True,
    ):
        st.session_state.coletando = True
        st.session_state.percurso_finalizado = False
        st.session_state.historico = pd.DataFrame(columns=["tempo", "aceleracao", "frenagem"])
        st.session_state.total_aceleracoes = 0
        st.session_state.total_frenagens = 0
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
        st.info("🟢 Coletando dados em tempo real...")
    elif st.session_state.conectado:
        st.info("🟡 Conectado. Pressione 'Iniciar Percurso' para começar a coleta.")
    else:
        st.warning("🔴 Desconectado. Conecte a porta serial na barra lateral.")

st.divider()


# ----------------------------------------------------------------------
# Métricas
# ----------------------------------------------------------------------
col_m1, col_m2, col_m3 = st.columns(3)
col_m1.metric("🚨 Frenagens Bruscas", int(st.session_state.total_frenagens))
col_m2.metric("⚠️ Acelerações Indevidas", int(st.session_state.total_aceleracoes))
col_m3.metric(
    "📊 Total de Eventos",
    int(st.session_state.total_aceleracoes + st.session_state.total_frenagens),
)

st.divider()


# ----------------------------------------------------------------------
# Fragmento com atualização automática (leitura em tempo real)
# ----------------------------------------------------------------------
@st.fragment(run_every=0.5)
def painel_tempo_real():
    if st.session_state.coletando:
        ler_novas_linhas()

    historico = st.session_state.historico

    st.subheader("Gráficos em Tempo Real")
    if historico.empty:
        st.write("Aguardando dados do sensor...")
    else:
        ultimos = historico.tail(200).set_index("tempo")
        col_g1, col_g2 = st.columns(2)
        with col_g1:
            st.caption("Aceleração (m/s²)")
            st.line_chart(ultimos["aceleracao"])
        with col_g2:
            st.caption("Frenagem (m/s²)")
            st.line_chart(ultimos["frenagem"])

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

    col_voz1, col_voz2 = st.columns([1, 3])
    with col_voz1:
        if st.button("🔊 Ouvir Feedback"):
            falar_texto(st.session_state.ultimo_resumo)

    if not TTS_DISPONIVEL:
        st.caption(
            "⚠️ pyttsx3 não encontrado. Instale com `pip install pyttsx3` "
            "para habilitar o feedback em áudio."
        )
