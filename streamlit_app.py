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
        "hora_inicio_percurso": None,
        "nota_ultima_corrida": None,
        "pontos_ultima_corrida": 0,
        "saldo_pontos_ano": 0,
        "historico_percursos": [],  # cada item: data, duracao_min, eventos, nota, pontos
        "resumo_pagina": 0,  # 0 = resumo escrito, 1 = pontos e descontos
        "mostrar_checklist": False,
        "mostrar_resumo": False,
    }
    for chave, valor in defaults.items():
        if chave not in st.session_state:
            st.session_state[chave] = valor


inicializar_estado()


# ----------------------------------------------------------------------
# Simulação de dados (substitui o Arduino por enquanto)
# ----------------------------------------------------------------------
def gerar_leitura_simulada(chance_evento):
    """Gera um valor de aceleração longitudinal parecido com um robô real:
    a maior parte do tempo é ruído pequeno, com "rajadas" ocasionais de
    aceleração ou frenagem brusca, como se fosse um trajeto de verdade.

    chance_evento: probabilidade (0 a 1) de iniciar uma nova rajada a cada
    ciclo de leitura. Quanto maior, mais "arriscado" o motorista simulado."""

    # chance de começar uma nova rajada de evento, se não houver uma ativa
    if st.session_state.sim_tipo_ativo is None and random.random() < chance_evento:
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
        base = (
            "Percurso finalizado. Nenhum evento de condução brusca foi registrado. "
            "Excelente condução, dentro dos padrões de segurança."
        )
    elif total_eventos <= 3:
        base = "Percurso finalizado. A condução foi, no geral, tranquila, com poucos eventos."
    elif total_eventos <= 8:
        base = (
            "Percurso finalizado. A condução apresentou um número moderado de eventos bruscos. "
            "Atenção redobrada é recomendada."
        )
    else:
        base = (
            "Percurso finalizado. A condução apresentou muitos eventos bruscos. "
            "É recomendado revisar o comportamento ao volante."
        )

    nota = st.session_state.nota_ultima_corrida
    pontos = st.session_state.pontos_ultima_corrida
    if nota is not None:
        base += f" Nota da corrida: {nota:.1f} de 10. Você ganhou {int(pontos)} pontos de recompensa."

    return base


# ----------------------------------------------------------------------
# Sistema de recompensa: nota de qualidade (0-10) e pontos resgatáveis
# ----------------------------------------------------------------------
def calcular_pontuacao(duracao_min, total_eventos, peso_penalidade, pontos_por_minuto, limiar_minimo):
    """
    nota_qualidade (0-10): baseada na TAXA de eventos por minuto, não no total
    bruto — assim uma corrida longa não é penalizada só por durar mais tempo.

    pontos_corrida: a "moeda" resgatável. Usa a duração como um substituto
    (proxy) da quilometragem, já que ainda não há sensor de distância
    (encoder de roda) no robô. Quando ele for adicionado, troque
    'duracao_min' por 'km_percorridos' nesta função.

    Corridas com nota abaixo de 'limiar_minimo' não geram pontos, para não
    recompensar uma condução muito perigosa só porque durou bastante tempo.
    """
    duracao_para_taxa = max(duracao_min, 1 / 6)  # evita divisão por corridas de poucos segundos
    taxa_eventos_por_min = total_eventos / duracao_para_taxa

    nota_qualidade = 10 - (taxa_eventos_por_min * peso_penalidade)
    nota_qualidade = max(0.0, min(10.0, nota_qualidade))

    if nota_qualidade < limiar_minimo:
        pontos_corrida = 0
    else:
        pontos_corrida = duracao_min * pontos_por_minuto * (nota_qualidade / 10)

    return round(nota_qualidade, 1), round(pontos_corrida)


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

# Constantes do sistema de recompensa (usadas tanto no pop-up de resumo
# quanto na seção de resgate mais abaixo na tela)
VALOR_POR_PONTO_IPVA = 0.05  # R$ que cada ponto vale em desconto de IPVA
DESCONTO_MAXIMO_IPVA_PCT = 20  # limite regulatório hipotético, ajuste conforme a regra real do estado
POSTOS_EXEMPLO = {
    "Posto Central — R$ 5 de desconto": 100,
    "Auto Posto Bairro Sul — R$ 10 de desconto": 190,
    "Rede Estrada Verde — R$ 20 de desconto": 350,
}


def iniciar_percurso_de_verdade():
    """Zera os contadores e efetivamente começa a coleta de dados."""
    st.session_state.coletando = True
    st.session_state.percurso_finalizado = False
    st.session_state.historico = pd.DataFrame(columns=["tempo", "accel_x", "forca_g"])
    st.session_state.total_aceleracoes = 0
    st.session_state.total_frenagens = 0
    st.session_state.estado_evento_anterior = "neutro"
    st.session_state.sim_tipo_ativo = None
    st.session_state.hora_inicio_percurso = datetime.now()


@st.dialog("✅ Checklist Pré-Corrida")
def checklist_dialog():
    st.write("Confira todos os itens antes de iniciar a corrida:")
    marcados = []
    for item in ITENS_CHECKLIST:
        marcados.append(st.checkbox(item, key=f"check_{item}"))

    todos_marcados = all(marcados)
    if not todos_marcados:
        st.caption("Marque todos os itens para liberar o início da corrida.")

    col_confirmar, col_cancelar = st.columns([3, 1])
    with col_confirmar:
        if st.button("🚦 Confirmar e Iniciar Corrida", disabled=not todos_marcados, type="primary"):
            for item in ITENS_CHECKLIST:
                st.session_state.pop(f"check_{item}", None)  # limpa as marcações para a próxima corrida
            st.session_state.mostrar_checklist = False
            iniciar_percurso_de_verdade()
            st.rerun()
    with col_cancelar:
        if st.button("Cancelar"):
            st.session_state.mostrar_checklist = False
            st.rerun()


@st.dialog("📋 Resumo da Corrida")
def resumo_dialog():
    if st.session_state.resumo_pagina == 0:
        st.write(st.session_state.ultimo_resumo)

        if st.button("🔊 Ouvir Feedback"):
            falar_no_navegador(st.session_state.ultimo_resumo)

        st.write("")
        col_fechar, col_meio, col_dir = st.columns([1, 2, 1])
        with col_fechar:
            if st.button("✖ Fechar"):
                st.session_state.mostrar_resumo = False
                st.rerun()
        with col_dir:
            if st.button("Pontos ➡️"):
                st.session_state.resumo_pagina = 1
                st.rerun()

    else:
        nota = st.session_state.nota_ultima_corrida
        pontos = st.session_state.pontos_ultima_corrida

        st.metric("⭐ Nota da Corrida", f"{nota:.1f}/10" if nota is not None else "—")
        st.metric("🏆 Pontos Ganhos Nesta Corrida", int(pontos))
        st.metric("💰 Saldo Total de Pontos (Ano)", int(st.session_state.saldo_pontos_ano))

        st.divider()
        st.caption("Troque seus pontos agora:")

        aba_ipva, aba_posto = st.tabs(["🚗 Desconto no IPVA", "⛽ Postos Parceiros"])

        with aba_ipva:
            valor_ipva_dialog = st.number_input(
                "Valor do IPVA (R$)", min_value=0.0, value=800.0, step=50.0, key="dialog_valor_ipva"
            )
            desconto_bruto = st.session_state.saldo_pontos_ano * VALOR_POR_PONTO_IPVA
            desconto_maximo = valor_ipva_dialog * (DESCONTO_MAXIMO_IPVA_PCT / 100)
            desconto_final = min(desconto_bruto, desconto_maximo)
            pontos_usados_ipva = round(desconto_final / VALOR_POR_PONTO_IPVA) if VALOR_POR_PONTO_IPVA else 0

            st.write(f"Desconto disponível: **R$ {desconto_final:.2f}** ({pontos_usados_ipva} pontos)")
            if desconto_bruto > desconto_maximo:
                st.caption(f"(limitado ao teto de {DESCONTO_MAXIMO_IPVA_PCT}% do valor do IPVA)")

            if st.button("Resgatar no IPVA", key="dialog_btn_ipva"):
                if st.session_state.saldo_pontos_ano <= 0:
                    st.warning("Você ainda não tem pontos suficientes.")
                else:
                    st.session_state.saldo_pontos_ano -= pontos_usados_ipva
                    st.success(f"Resgatado! R$ {desconto_final:.2f} de desconto.")
                    st.rerun()

        with aba_posto:
            escolha_posto_dialog = st.selectbox(
                "Posto parceiro (exemplos)", list(POSTOS_EXEMPLO.keys()), key="dialog_posto_select"
            )
            custo_posto = POSTOS_EXEMPLO[escolha_posto_dialog]
            st.write(f"Custo: **{custo_posto} pontos**")

            if st.button("Resgatar no posto", key="dialog_btn_posto"):
                if st.session_state.saldo_pontos_ano >= custo_posto:
                    st.session_state.saldo_pontos_ano -= custo_posto
                    st.success(f"Cupom gerado: {escolha_posto_dialog}")
                    st.rerun()
                else:
                    st.warning(
                        f"Pontos insuficientes. Você tem {int(st.session_state.saldo_pontos_ano)}, "
                        f"precisa de {custo_posto}."
                    )

        st.divider()
        col_esq, col_dir = st.columns([1, 3])
        with col_esq:
            if st.button("⬅️ Voltar", key="dialog_btn_voltar"):
                st.session_state.resumo_pagina = 0
                st.rerun()
        with col_dir:
            if st.button("✖ Fechar", key="dialog_btn_fechar_p2"):
                st.session_state.mostrar_resumo = False
                st.rerun()


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

st.sidebar.divider()
st.sidebar.subheader("🏆 Sistema de Recompensa")
peso_penalidade = st.sidebar.slider(
    "Peso da penalidade por evento", 0.5, 5.0, 2.5, 0.1,
    help="Quantos pontos a nota (0-10) perde para cada evento por minuto de condução.",
)
pontos_por_minuto = st.sidebar.slider(
    "Pontos base por minuto rodado", 1, 30, 10, 1,
    help="Pontos ganhos por minuto de percurso quando a nota é 10 (perfeita). Serve de proxy para quilometragem até termos um sensor de distância.",
)
limiar_minimo_pontuavel = st.sidebar.slider(
    "Nota mínima para ganhar pontos", 0.0, 10.0, 3.0, 0.5,
    help="Corridas com nota abaixo deste valor não geram pontos, mesmo que sejam longas.",
)

st.sidebar.divider()
st.sidebar.subheader("🎮 Simulação")
chance_evento_pct = st.sidebar.slider(
    "Quão arriscado é o motorista simulado (%)", 0.5, 15.0, 2.0, 0.5,
    help="Chance de começar um evento brusco a cada ciclo de leitura. Valores baixos = motorista cuidadoso (gera pontos). Valores altos = motorista arriscado (nota despenca).",
)
chance_evento = chance_evento_pct / 100


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
        st.session_state.mostrar_checklist = True

with col_b:
    if st.button(
        "⏹️ Finalizar Percurso",
        disabled=not st.session_state.coletando,
        use_container_width=True,
        type="primary",
    ):
        st.session_state.coletando = False
        st.session_state.percurso_finalizado = True

        hora_fim = datetime.now()
        hora_inicio = st.session_state.hora_inicio_percurso or hora_fim
        duracao_min = max((hora_fim - hora_inicio).total_seconds() / 60, 0.01)
        total_eventos = st.session_state.total_aceleracoes + st.session_state.total_frenagens

        nota, pontos = calcular_pontuacao(
            duracao_min, total_eventos, peso_penalidade, pontos_por_minuto, limiar_minimo_pontuavel
        )
        st.session_state.nota_ultima_corrida = nota
        st.session_state.pontos_ultima_corrida = pontos
        st.session_state.saldo_pontos_ano += pontos
        st.session_state.historico_percursos.append(
            {
                "Data": hora_fim.strftime("%d/%m/%Y %H:%M"),
                "Duração (min)": round(duracao_min, 1),
                "Eventos": total_eventos,
                "Nota": nota,
                "Pontos ganhos": pontos,
            }
        )

        st.session_state.ultimo_resumo = gerar_resumo_texto()
        st.session_state.resumo_pagina = 0
        st.session_state.mostrar_resumo = True

with col_c:
    if st.session_state.coletando:
        st.info("🟢 Simulando percurso em tempo real...")
    else:
        st.info("🟡 Pressione 'Iniciar Percurso' para começar a simulação.")

# Reabre o pop-up certo em TODA execução do script, enquanto a flag
# estiver ligada — é isso que faz a navegação por seta dentro do
# pop-up funcionar sem ele fechar sozinho.
if st.session_state.mostrar_checklist:
    checklist_dialog()

if st.session_state.mostrar_resumo:
    resumo_dialog()

st.divider()


# ----------------------------------------------------------------------
# Métricas
# ----------------------------------------------------------------------
col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
col_m1.metric("🚨 Frenagens Bruscas", int(st.session_state.total_frenagens))
col_m2.metric("⚠️ Acelerações Indevidas", int(st.session_state.total_aceleracoes))
col_m3.metric("📊 Total de Eventos", int(st.session_state.total_aceleracoes + st.session_state.total_frenagens))
nota_exibida = st.session_state.nota_ultima_corrida
col_m4.metric("⭐ Nota da Última Corrida", f"{nota_exibida:.1f}/10" if nota_exibida is not None else "—")
col_m5.metric("🏆 Saldo de Pontos (Ano)", int(st.session_state.saldo_pontos_ano))

st.divider()


# ----------------------------------------------------------------------
# Painel em tempo real (auto-atualiza a cada 0.5s)
# ----------------------------------------------------------------------
@st.fragment(run_every=0.5)
def painel_tempo_real():
    if st.session_state.coletando:
        accel_x, forca_g = gerar_leitura_simulada(chance_evento)
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
# Extrato anual de pontos
# ----------------------------------------------------------------------
st.divider()
st.subheader("📅 Extrato Anual de Pontos")

if not st.session_state.historico_percursos:
    st.write("Nenhuma corrida registrada ainda nesta sessão. Finalize um percurso para começar o extrato.")
else:
    df_extrato = pd.DataFrame(st.session_state.historico_percursos)
    st.dataframe(df_extrato, use_container_width=True, hide_index=True)
    st.caption(
        f"Saldo acumulado no ano: **{int(st.session_state.saldo_pontos_ano)} pontos** "
        f"em {len(st.session_state.historico_percursos)} corrida(s)."
    )
    st.info(
        "⚠️ Este extrato existe apenas durante esta sessão do navegador (dados simulados/demo). "
        "Para valer durante o ano todo, de verdade, precisa de um cadastro de motorista "
        "e um banco de dados permanente (ex: o mesmo Firebase já usado para os dados do robô)."
    )

st.divider()
st.subheader("🎁 Resgatar Pontos")

col_ipva, col_posto = st.columns(2)

with col_ipva:
    st.markdown("**Desconto no IPVA**")
    valor_ipva = st.number_input("Valor do IPVA do veículo (R$)", min_value=0.0, value=800.0, step=50.0)
    desconto_bruto = st.session_state.saldo_pontos_ano * VALOR_POR_PONTO_IPVA
    desconto_maximo = valor_ipva * (DESCONTO_MAXIMO_IPVA_PCT / 100)
    desconto_final = min(desconto_bruto, desconto_maximo)
    pontos_usados_ipva = round(desconto_final / VALOR_POR_PONTO_IPVA) if VALOR_POR_PONTO_IPVA else 0

    st.write(f"Com seus {int(st.session_state.saldo_pontos_ano)} pontos, seu desconto seria de **R$ {desconto_final:.2f}**")
    if desconto_bruto > desconto_maximo:
        st.caption(f"(limitado ao teto de {DESCONTO_MAXIMO_IPVA_PCT}% do valor do IPVA)")

    if st.button("Resgatar desconto de IPVA"):
        if st.session_state.saldo_pontos_ano <= 0:
            st.warning("Você ainda não tem pontos suficientes.")
        else:
            st.session_state.saldo_pontos_ano -= pontos_usados_ipva
            st.success(f"Resgatado! R$ {desconto_final:.2f} de desconto usando {pontos_usados_ipva} pontos.")
            st.rerun()

with col_posto:
    st.markdown("**Desconto em postos parceiros**")
    escolha_posto = st.selectbox("Posto parceiro (exemplos)", list(POSTOS_EXEMPLO.keys()))
    custo_pontos = POSTOS_EXEMPLO[escolha_posto]
    st.write(f"Custo: **{custo_pontos} pontos**")

    if st.button("Resgatar no posto"):
        if st.session_state.saldo_pontos_ano >= custo_pontos:
            st.session_state.saldo_pontos_ano -= custo_pontos
            st.success(f"Resgatado! Cupom gerado para: {escolha_posto}")
            st.rerun()
        else:
            st.warning(
                f"Pontos insuficientes. Você tem {int(st.session_state.saldo_pontos_ano)}, "
                f"precisa de {custo_pontos}."
            )
