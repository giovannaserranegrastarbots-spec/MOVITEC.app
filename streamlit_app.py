
import json
import re
import time
from datetime import datetime

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="MOVTEC", layout="wide")


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
        "historico_percursos": [],  # cada item: data, duracao_min, eventos, nota, pontos
        "resumo_pagina": 0,  # 0 = resumo escrito, 1 = pontos e descontos
        "mostrar_checklist": False,
        "mostrar_resumo": False,
        "nome_motorista": "",
        "ultimo_finalizado_processado": None,  # marca da última finalização já tratada
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
def buscar_estado():
    """Busca o retrato mais recente da telemetria, enviado pela ponte local."""
    if not firebase_configurado():
        return {}
    try:
        resposta = requests.get(f"{FIREBASE_URL}/estado.json", timeout=5)
        resposta.raise_for_status()
        return resposta.json() or {}
    except requests.exceptions.RequestException:
        st.warning("⚠️ Não consegui falar com o Firebase agora. Verifique sua conexão.")
        return {}
    except Exception:
        return {}


def enviar_comando(acao):
    """Escreve um comando (iniciar/finalizar) para a ponte local obedecer.
    Cada comando tem um 'id' único (baseado no horário) para a ponte saber
    que é um comando NOVO, e não o mesmo de antes."""
    if not firebase_configurado():
        st.error(
            "FIREBASE_URL não configurado neste app — sem isso, não há como avisar a ponte local. "
            "Edite a constante no topo do arquivo."
        )
        return
    payload = {"acao": acao, "id": f"{acao}_{int(time.time() * 1000)}"}
    try:
        resposta = requests.put(f"{FIREBASE_URL}/comando.json", data=json.dumps(payload), timeout=5)
        resposta.raise_for_status()
    except requests.exceptions.RequestException:
        st.warning(
            "⚠️ Não consegui enviar o comando para o robô agora (sem conexão com o Firebase). "
            "Confira se o notebook da ponte (ponte_local.py) está ligado e conectado à internet."
        )
    except Exception:
        st.warning("⚠️ Algo deu errado ao enviar o comando.")


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


def calcular_indice_qualidade_anual():
    """
    Índice de qualidade do ano inteiro: média das notas de todas as
    corridas já finalizadas, ponderada pela duração de cada uma.

        indice = Σ(nota_i × duração_i) / Σ(duração_i)

    Diferente dos "pontos" (que são uma moeda que se acumula e se gasta),
    esse índice é recalculado do zero a cada corrida — ele não mede volume,
    mede CONSISTÊNCIA da qualidade ao longo do tempo. Duas corridas com a
    mesma nota, uma de 2h e outra de 8h, resultam no mesmo índice; só um
    histórico de notas mais baixas puxa o índice pra baixo de verdade.

    Usamos esse índice para modular o teto de desconto do seguro (ver mais
    abaixo), então quem tem qualidade consistente libera mais do teto
    negociado com a seguradora do que quem rodou irregular.
    """
    historico = st.session_state.historico_percursos
    if not historico:
        return None

    soma_ponderada = sum(item["Nota"] * item["Duração (min)"] for item in historico)
    soma_duracao = sum(item["Duração (min)"] for item in historico)

    if soma_duracao <= 0:
        return None

    return round(soma_ponderada / soma_duracao, 1)


def calcular_teto_seguro_modulado():
    """
    O teto de desconto (DESCONTO_MAXIMO_SEGURO_PCT) é definido pela política
    comercial da seguradora parceira, mas quanto DESSE teto o motorista
    consegue de fato usar é modulado pelo índice de qualidade anual: índice
    10/10 libera 100% do teto, índice 5/10 libera só 50%, etc. Isso amarra o
    benefício do seguro à consistência da boa condução ao longo do ano, não
    só ao volume de pontos acumulados.

    Retorna (teto_efetivo_pct, indice_anual). indice_anual vem None se ainda
    não houver nenhuma corrida no histórico.
    """
    indice_anual = calcular_indice_qualidade_anual()
    if indice_anual is None:
        return 0.0, None
    teto_efetivo_pct = DESCONTO_MAXIMO_SEGURO_PCT * (indice_anual / 10)
    return teto_efetivo_pct, indice_anual


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
VALOR_POR_PONTO_SEGURO = 0.05  # R$ que cada ponto vale em desconto de seguro
DESCONTO_MAXIMO_SEGURO_PCT = 20  # teto hipotético, ajuste conforme o acordo real com a seguradora parceira
POSTOS_EXEMPLO = {
    "Posto Central — R$ 5 de desconto": 100,
    "Auto Posto Bairro Sul — R$ 10 de desconto": 190,
    "Rede Estrada Verde — R$ 20 de desconto": 350,
}

# ---------- Configuração do Firebase (obrigatório nesta versão) ----------
# Aqui o Firebase não é mais opcional: ele carrega a telemetria real vinda
# da ponte local, os comandos de Iniciar/Finalizar, E o histórico de cada
# motorista. Cole a mesma URL usada no ponte_local.py.
FIREBASE_URL = "https://telemetria-app-281d2-default-rtdb.firebaseio.com/"


def firebase_configurado():
    return "SEU-PROJETO" not in FIREBASE_URL


def chave_motorista(nome):
    """Transforma o nome digitado numa chave válida para o Firebase
    (sem espaços, pontos, barras ou colchetes)."""
    chave = nome.strip().lower()
    chave = re.sub(r"[^a-z0-9_-]+", "_", chave)
    return chave or "motorista"


def carregar_dados_motorista(nome):
    """Busca o histórico salvo desse motorista no Firebase. Devolve None
    (silenciosamente, se o Firebase não estiver configurado; com um aviso
    amigável, se a busca falhar) para que o app sempre continue funcionando
    com os dados da sessão atual, mesmo sem conexão."""
    if not firebase_configurado():
        return None
    try:
        chave = chave_motorista(nome)
        resposta = requests.get(f"{FIREBASE_URL}/motoristas/{chave}.json", timeout=5)
        resposta.raise_for_status()
        return resposta.json()
    except requests.exceptions.RequestException:
        st.warning(
            "⚠️ Não consegui buscar seu histórico salvo agora (sem conexão com o banco). "
            "Vamos continuar só com os dados desta sessão."
        )
        return None
    except Exception:
        st.warning("⚠️ Algo deu errado ao buscar seus dados salvos. Continuando só com os dados desta sessão.")
        return None


def salvar_dados_motorista(nome, dados):
    """Salva o histórico do motorista no Firebase. Se não estiver configurado
    ou a gravação falhar, avisa de forma amigável mas não trava o app —
    os pontos da corrida continuam valendo nesta sessão de qualquer forma."""
    if not firebase_configurado():
        return
    try:
        chave = chave_motorista(nome)
        resposta = requests.put(f"{FIREBASE_URL}/motoristas/{chave}.json", data=json.dumps(dados), timeout=5)
        resposta.raise_for_status()
    except requests.exceptions.RequestException:
        st.warning(
            "⚠️ Não consegui salvar seu histórico agora (sem conexão com o banco). "
            "Seus pontos desta corrida continuam valendo aqui, mas talvez não apareçam da próxima vez que você entrar."
        )
    except Exception:
        st.warning("⚠️ Algo deu errado ao salvar seus dados desta corrida.")


def carregar_todos_motoristas():
    """Busca o histórico de TODOS os motoristas já salvos, para montar o
    comparativo. Devolve None se o Firebase não estiver configurado ou a
    busca falhar (com aviso amigável nesse segundo caso)."""
    if not firebase_configurado():
        return None
    try:
        resposta = requests.get(f"{FIREBASE_URL}/motoristas.json", timeout=5)
        resposta.raise_for_status()
        return resposta.json() or {}
    except Exception:
        st.warning("⚠️ Não consegui carregar o comparativo entre motoristas agora. Tente novamente em instantes.")
        return None


def salvar_estado_atual():
    """Atalho para salvar o histórico/saldo atuais do motorista logado."""
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
    """Avisa a ponte local (via comando no Firebase) que a corrida deve
    começar. Também reflete otimistamente na tela local — o próximo
    ciclo do painel em tempo real confirma com o estado real da ponte."""
    enviar_comando("iniciar")
    st.session_state.coletando = True
    st.session_state.percurso_finalizado = False
    st.session_state.historico = pd.DataFrame(columns=["tempo", "accel_x", "forca_g"])
    st.session_state.total_aceleracoes = 0
    st.session_state.total_frenagens = 0


@st.dialog("✅ Checklist Pré-Corrida")
def checklist_dialog():
    nome_digitado = st.text_input(
        "Nome do motorista", value=st.session_state.nome_motorista, key="input_nome_motorista"
    )

    st.write("Confira todos os itens antes de iniciar a corrida:")
    marcados = []
    for item in ITENS_CHECKLIST:
        marcados.append(st.checkbox(item, key=f"check_{item}"))

    todos_marcados = all(marcados)
    nome_valido = nome_digitado.strip() != ""

    if not nome_valido:
        st.caption("Digite o nome do motorista para liberar o início da corrida.")
    elif not todos_marcados:
        st.caption("Marque todos os itens para liberar o início da corrida.")

    col_confirmar, col_cancelar = st.columns([3, 1])
    with col_confirmar:
        if st.button(
            "🚦 Confirmar e Iniciar Corrida",
            disabled=not (todos_marcados and nome_valido),
            type="primary",
        ):
            for item in ITENS_CHECKLIST:
                st.session_state.pop(f"check_{item}", None)  # limpa as marcações para a próxima corrida
            nome = nome_digitado.strip()
            st.session_state.nome_motorista = nome
            st.session_state.mostrar_checklist = False

            dados_salvos = carregar_dados_motorista(nome)
            if dados_salvos:
                st.session_state.historico_percursos = dados_salvos.get("historico_percursos", [])
                st.session_state.saldo_pontos_ano = dados_salvos.get("saldo_pontos_ano", 0)

            iniciar_percurso_de_verdade()
            st.rerun()
    with col_cancelar:
        if st.button("Cancelar"):
            st.session_state.mostrar_checklist = False
            st.rerun()


@st.dialog("📋 Resumo da Corrida")
def resumo_dialog():
    if st.session_state.resumo_pagina == 0:
        st.success("✅ Percurso concluído com sucesso! Dados recebidos e processados.")
        st.caption(
            f"🕒 {st.session_state.horario_ultima_corrida}  •  "
            f"⏱️ {st.session_state.duracao_ultima_corrida:.2f} min  •  "
            f"📡 {st.session_state.amostras_ultima_corrida} amostras coletadas"
        )

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

        aba_seguro, aba_posto = st.tabs(["🚗 Desconto no Seguro", "⛽ Postos Parceiros"])

        with aba_seguro:
            valor_seguro_dialog = st.number_input(
                "Valor da apólice (R$)", min_value=0.0, value=800.0, step=50.0, key="dialog_valor_seguro"
            )
            teto_pct_dialog, indice_dialog = calcular_teto_seguro_modulado()
            desconto_bruto = st.session_state.saldo_pontos_ano * VALOR_POR_PONTO_SEGURO
            desconto_maximo = valor_seguro_dialog * (teto_pct_dialog / 100)
            desconto_final = min(desconto_bruto, desconto_maximo)
            pontos_usados_seguro = round(desconto_final / VALOR_POR_PONTO_SEGURO) if VALOR_POR_PONTO_SEGURO else 0

            if indice_dialog is not None:
                st.caption(f"Índice de qualidade anual: {indice_dialog:.1f}/10 → libera {teto_pct_dialog:.1f}% do teto de {DESCONTO_MAXIMO_SEGURO_PCT}%")
            st.write(f"Desconto disponível: **R$ {desconto_final:.2f}** ({pontos_usados_seguro} pontos)")
            if desconto_bruto > desconto_maximo:
                st.caption(f"(limitado pelo seu teto atual de {teto_pct_dialog:.1f}% do valor do seguro)")

            if st.button("Resgatar no Seguro", key="dialog_btn_seguro"):
                if st.session_state.saldo_pontos_ano <= 0:
                    st.warning("Você ainda não tem pontos suficientes.")
                else:
                    st.session_state.saldo_pontos_ano -= pontos_usados_seguro
                    salvar_estado_atual()
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
                    salvar_estado_atual()
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
if firebase_configurado():
    st.sidebar.success("🟢 Firebase configurado — pronto para receber dados reais da ponte local.")
else:
    st.sidebar.error(
        "🔴 FIREBASE_URL não configurado neste arquivo. Edite a constante no topo do código "
        "com a mesma URL usada no ponte_local.py."
    )

st.sidebar.caption(
    "Os limiares de aceleração/frenagem brusca agora são configurados diretamente no "
    "ponte_local.py (constantes LIMIAR_ACELERACAO, LIMIAR_FRENAGEM, MARGEM_HISTERESE), "
    "já que é lá que a detecção de eventos acontece de verdade."
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



# ----------------------------------------------------------------------
# Cabeçalho
# ----------------------------------------------------------------------
st.title("MOVTEC - SISTEMA DE TELEMETRIA")
st.caption("Monitoramento de acelerações e frenagens bruscas — versão demo com dados simulados")

if "viu_boas_vindas" not in st.session_state:
    st.session_state.viu_boas_vindas = False

with st.expander("ℹ️ Sobre este projeto", expanded=not st.session_state.viu_boas_vindas):
    st.markdown(
        """
Este painel acompanha, em tempo real, o comportamento de condução de um **robô educacional**
equipado com um sensor MPU6050 (acelerômetro/giroscópio), com o objetivo de conscientizar
sobre direção segura.

**Como funciona:**
1. Preencha o checklist de segurança e inicie o percurso.
2. Acompanhe os gráficos de aceleração, frenagem e força G em tempo real.
3. Ao finalizar, veja seu resumo, sua nota (0 a 10) e os pontos ganhos.
4. Troque os pontos por desconto no seguro ou em postos parceiros (exemplos fictícios, só para demonstração).

*(Os dados vêm de verdade do sensor MPU6050 do robô, transmitidos por Bluetooth até o notebook da ponte, e daí até aqui pelo Firebase.)*
        """
    )
st.session_state.viu_boas_vindas = True

st.divider()


# ----------------------------------------------------------------------
# Controles de percurso
# ----------------------------------------------------------------------
col_a, col_b, col_c = st.columns([1, 1, 2])

with col_a:
    if st.button("▶️ Iniciar Percurso", disabled=st.session_state.coletando, use_container_width=True):
        st.session_state.mostrar_resumo = False  # garante que um resumo antigo "preso" não bloqueie o checklist
        st.session_state.mostrar_checklist = True

with col_b:
    if st.button(
        "⏹️ Finalizar Percurso",
        disabled=not st.session_state.coletando,
        use_container_width=True,
        type="primary",
    ):
        enviar_comando("finalizar")
        st.session_state.coletando = False

with col_c:
    if st.session_state.coletando:
        st.info("🟢 Recebendo dados em tempo real do robô...")
    else:
        st.info("🟡 Pressione 'Iniciar Percurso' para começar.")

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
# Painel em tempo real (busca a telemetria da ponte local a cada 2s)
# ----------------------------------------------------------------------
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

        # Detecta uma finalização NOVA (a ponte só manda "finalizado_em"
        # quando o percurso termina) e calcula nota/pontos nesse momento —
        # é aqui, e não no clique do botão, porque só agora temos os
        # números finais de verdade, vindos da ponte.
        marca_finalizacao = estado.get("finalizado_em")
        if (
            estado.get("finalizado")
            and marca_finalizacao
            and marca_finalizacao != st.session_state.ultimo_finalizado_processado
        ):
            duracao_min = estado.get("duracao_min", 0.01)
            total_eventos = st.session_state.total_aceleracoes + st.session_state.total_frenagens
            amostras_coletadas = len(amostras)

            nota, pontos = calcular_pontuacao(
                duracao_min, total_eventos, peso_penalidade, pontos_por_minuto, limiar_minimo_pontuavel
            )
            st.session_state.nota_ultima_corrida = nota
            st.session_state.pontos_ultima_corrida = pontos
            st.session_state.saldo_pontos_ano += pontos
            st.session_state.amostras_ultima_corrida = amostras_coletadas
            st.session_state.duracao_ultima_corrida = round(duracao_min, 2)
            st.session_state.horario_ultima_corrida = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            st.session_state.historico_percursos.append(
                {
                    "Data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                    "Duração (min)": round(duracao_min, 1),
                    "Eventos": total_eventos,
                    "Nota": nota,
                    "Pontos ganhos": pontos,
                }
            )
            st.session_state.ultimo_resumo = gerar_resumo_texto()
            st.session_state.resumo_pagina = 0
            st.session_state.mostrar_resumo = True
            st.session_state.ultimo_finalizado_processado = marca_finalizacao
            st.session_state.percurso_finalizado = True

            salvar_estado_atual()

            # Força uma execução completa do script AGORA (em vez de esperar
            # a próxima interação) para o pop-up de resumo abrir na hora.
            st.rerun()

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

    indice_anual = calcular_indice_qualidade_anual()
    col_ext1, col_ext2 = st.columns(2)
    col_ext1.metric("💰 Saldo de Pontos (Ano)", int(st.session_state.saldo_pontos_ano))
    col_ext2.metric("📈 Índice de Qualidade Anual", f"{indice_anual:.1f}/10" if indice_anual is not None else "—")
    st.caption(
        f"{len(st.session_state.historico_percursos)} corrida(s) registrada(s). "
        "O índice de qualidade é a média das notas ponderada pela duração de cada corrida — "
        "ele é quem define quanto do teto de desconto do seguro você consegue usar (veja abaixo)."
    )
    if firebase_configurado():
        st.success(
            f"✅ Histórico salvo com persistência real, associado ao motorista "
            f"**{st.session_state.nome_motorista or 'sem nome'}**. Ele continua aqui mesmo se você fechar e voltar depois."
        )
    else:
        st.info(
            "⚠️ Este extrato existe apenas durante esta sessão do navegador. "
            "Para valer entre visitas diferentes, de verdade, configure a constante FIREBASE_URL "
            "no topo do arquivo (mesma URL usada no ponte_local.py)."
        )

st.divider()
st.subheader("🎁 Resgatar Pontos")

col_seguro, col_posto = st.columns(2)

with col_seguro:
    st.markdown("**Desconto no Seguro**")
    valor_seguro = st.number_input("Valor da apólice de seguro (R$)", min_value=0.0, value=800.0, step=50.0)
    teto_pct, indice_para_seguro = calcular_teto_seguro_modulado()
    desconto_bruto = st.session_state.saldo_pontos_ano * VALOR_POR_PONTO_SEGURO
    desconto_maximo = valor_seguro * (teto_pct / 100)
    desconto_final = min(desconto_bruto, desconto_maximo)
    pontos_usados_seguro = round(desconto_final / VALOR_POR_PONTO_SEGURO) if VALOR_POR_PONTO_SEGURO else 0

    if indice_para_seguro is not None:
        st.caption(f"Índice de qualidade anual: {indice_para_seguro:.1f}/10 → libera {teto_pct:.1f}% do teto de {DESCONTO_MAXIMO_SEGURO_PCT}%")
    st.write(f"Com seus {int(st.session_state.saldo_pontos_ano)} pontos, seu desconto seria de **R$ {desconto_final:.2f}**")
    if desconto_bruto > desconto_maximo:
        st.caption(f"(limitado pelo seu teto atual de {teto_pct:.1f}% do valor do seguro)")

    if st.button("Resgatar desconto no seguro"):
        if st.session_state.saldo_pontos_ano <= 0:
            st.warning("Você ainda não tem pontos suficientes.")
        else:
            st.session_state.saldo_pontos_ano -= pontos_usados_seguro
            salvar_estado_atual()
            st.success(f"Resgatado! R$ {desconto_final:.2f} de desconto usando {pontos_usados_seguro} pontos.")
            st.rerun()

with col_posto:
    st.markdown("**Desconto em postos parceiros**")
    escolha_posto = st.selectbox("Posto parceiro (exemplos)", list(POSTOS_EXEMPLO.keys()))
    custo_pontos = POSTOS_EXEMPLO[escolha_posto]
    st.write(f"Custo: **{custo_pontos} pontos**")

    if st.button("Resgatar no posto"):
        if st.session_state.saldo_pontos_ano >= custo_pontos:
            st.session_state.saldo_pontos_ano -= custo_pontos
            salvar_estado_atual()
            st.success(f"Resgatado! Cupom gerado para: {escolha_posto}")
            st.rerun()
        else:
            st.warning(
                f"Pontos insuficientes. Você tem {int(st.session_state.saldo_pontos_ano)}, "
                f"precisa de {custo_pontos}."
            )


# ----------------------------------------------------------------------
# Comparativo entre motoristas
# ----------------------------------------------------------------------
st.divider()
st.subheader("🏁 Comparativo entre Motoristas")

if not firebase_configurado():
    st.info(
        "Este comparativo precisa da persistência com Firebase ativada (veja a constante "
        "FIREBASE_URL no topo do arquivo). Sem ela, cada sessão só enxerga os próprios dados."
    )
else:
    todos_motoristas = carregar_todos_motoristas()
    if not todos_motoristas:
        st.write("Ainda não há corridas salvas de nenhum motorista.")
    else:
        linhas_comparativo = []
        for chave, dados in todos_motoristas.items():
            if not isinstance(dados, dict):
                continue
            hist = dados.get("historico_percursos", []) or []
            soma_pond = sum(item.get("Nota", 0) * item.get("Duração (min)", 0) for item in hist)
            soma_dur = sum(item.get("Duração (min)", 0) for item in hist)
            indice = round(soma_pond / soma_dur, 1) if soma_dur > 0 else None
            linhas_comparativo.append(
                {
                    "Motorista": dados.get("nome", chave),
                    "Corridas": len(hist),
                    "Índice de Qualidade": indice,
                    "Saldo de Pontos": dados.get("saldo_pontos_ano", 0),
                }
            )

        if not linhas_comparativo:
            st.write("Ainda não há corridas salvas de nenhum motorista.")
        else:
            df_comparativo = pd.DataFrame(linhas_comparativo).sort_values(
                "Índice de Qualidade", ascending=False, na_position="last"
            )
            st.dataframe(df_comparativo, use_container_width=True, hide_index=True)

            df_grafico = df_comparativo.dropna(subset=["Índice de Qualidade"]).set_index("Motorista")
            if not df_grafico.empty:
                st.caption("Índice de qualidade anual por motorista")
                st.bar_chart(df_grafico["Índice de Qualidade"])
