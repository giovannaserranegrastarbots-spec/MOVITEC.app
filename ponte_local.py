"""
Ponte Local - Lê o Arduino (via Bluetooth HC-05) e obedece aos comandos
do site, repassando a telemetria para o Firebase
--------------------------------------------------------------------------
Esse script roda no notebook próximo ao robô (o que está pareado com o
HC-05 por Bluetooth) - ele NUNCA vai para o GitHub nem para a nuvem.

Diferente da primeira versão, ele agora NÃO decide sozinho quando começar
ou terminar um percurso. Ele fica esperando, obedece aos botões "Iniciar
Percurso" / "Finalizar Percurso" clicados no site (que escrevem um comando
no Firebase), e só então liga/desliga a leitura e o envio dos dados.

Fluxo:
    Site clica "Iniciar" -> escreve comando no Firebase
        -> ponte lê o comando -> começa a ler a serial e enviar telemetria
    Site clica "Finalizar" -> escreve comando no Firebase
        -> ponte lê o comando -> para de ler, calcula a duração total,
           envia o resultado final (para o site calcular nota/pontos)

Antes de rodar:
    1. Pareie o HC-05 com este computador pelo Bluetooth do sistema
       operacional (senha padrão geralmente 1234 ou 0000).
    2. Veja qual porta serial virtual foi criada para o HC-05 e configure
       em PORTA_SERIAL abaixo (recomendado, em vez de detecção automática).
    3. Cole a URL do seu Firebase Realtime Database em FIREBASE_URL.
    4. No terminal: pip install pyserial requests
    5. Rode: python ponte_local.py
    6. Deixe a janela aberta. Agora é só usar os botões no site.
"""

import json
import threading
import time
from collections import deque
from datetime import datetime

import requests
import serial
import serial.tools.list_ports

# ---------- CONFIGURAÇÃO: edite estas linhas ----------
FIREBASE_URL = "https://telemetria-app-281d2-default-rtdb.firebaseio.com/"

# Com HC-05 (Bluetooth), a detecção automática pode escolher a porta errada
# se você tiver outros dispositivos Bluetooth pareados. Recomendado: pareie o
# HC-05 primeiro, veja qual porta o sistema criou para ele, e defina aqui
# manualmente. Exemplos: "COM7" (Windows) ou "/dev/tty.HC-05-DevB" (Mac/Linux).
PORTA_SERIAL = None  # None = detectar automaticamente (só recomendado se for o único dispositivo pareado)
BAUD_RATE = 115200  # baud rate padrão de fábrica do HC-05

LIMIAR_ACELERACAO = 2.5
LIMIAR_FRENAGEM = 2.5
MARGEM_HISTERESE = 1.0

INTERVALO_ENVIO_TELEMETRIA_S = 1.0  # a cada quantos segundos envia a telemetria pro Firebase
INTERVALO_CHECAGEM_COMANDO_S = 1.0  # a cada quantos segundos confere se o site mandou algum comando novo
TAMANHO_JANELA = 60  # quantas amostras recentes mandar por vez
# --------------------------------------------------------

parar_programa = threading.Event()


def aguardar_tecla_para_encerrar():
    """Isso encerra o PROGRAMA INTEIRO (não um percurso específico) - é um
    botão de emergência local, caso o site fique inacessível no dia do evento."""
    input("\n>>> Pressione ENTER a qualquer momento para ENCERRAR o programa da ponte <<<\n\n")
    parar_programa.set()


def detectar_porta():
    portas = list(serial.tools.list_ports.comports())
    if not portas:
        return None
    return portas[0].device


def enviar_estado(payload):
    try:
        resposta = requests.put(f"{FIREBASE_URL}/estado.json", data=json.dumps(payload), timeout=5)
        resposta.raise_for_status()
    except Exception as erro:
        print(f"[aviso] Falha ao enviar telemetria para o Firebase: {erro}")


def buscar_comando():
    try:
        resposta = requests.get(f"{FIREBASE_URL}/comando.json", timeout=5)
        resposta.raise_for_status()
        return resposta.json()
    except Exception as erro:
        print(f"[aviso] Falha ao buscar comando do Firebase: {erro}")
        return None


def main():
    if "SEU-PROJETO" in FIREBASE_URL:
        print("ERRO: edite a constante FIREBASE_URL neste arquivo com a URL real do seu banco.")
        return

    porta = PORTA_SERIAL or detectar_porta()
    if not porta:
        print("Nenhuma porta serial encontrada. Pareie o HC-05 pelo Bluetooth e tente novamente.")
        return

    print(f"Conectando na porta {porta} ({BAUD_RATE} baud)...")
    try:
        conexao = serial.Serial(porta, BAUD_RATE, timeout=1)
    except Exception as erro:
        print(f"ERRO ao abrir a porta {porta}: {erro}")
        print("Confira se o HC-05 está pareado e se a porta configurada está certa.")
        return

    time.sleep(2)
    conexao.reset_input_buffer()
    print("Conectado! Aguardando comando 'Iniciar Percurso' vindo do site...\n")

    # Limpa qualquer estado "preso" de uma sessão anterior que não tenha
    # sido encerrada corretamente (ex: a ponte foi fechada no meio de uma
    # coleta). Sem isso, o site pode achar que já existe uma corrida em
    # andamento e desabilitar o botão "Iniciar Percurso".
    enviar_estado(
        {
            "coletando": False,
            "finalizado": False,
            "total_aceleracoes": 0,
            "total_frenagens": 0,
            "amostras": [],
        }
    )

    threading.Thread(target=aguardar_tecla_para_encerrar, daemon=True).start()

    coletando = False
    janela = deque(maxlen=TAMANHO_JANELA)
    total_aceleracoes = 0
    total_frenagens = 0
    estado_evento = "neutro"
    hora_inicio = None

    ultimo_envio_telemetria = 0.0
    ultima_checagem_comando = 0.0
    ultimo_comando_id_processado = None

    while not parar_programa.is_set():
        agora = time.time()

        # 1. Confere se chegou um comando novo do site (Iniciar/Finalizar)
        if agora - ultima_checagem_comando >= INTERVALO_CHECAGEM_COMANDO_S:
            ultima_checagem_comando = agora
            comando = buscar_comando()

            if comando and comando.get("id") != ultimo_comando_id_processado:
                acao = comando.get("acao")

                if acao == "iniciar" and not coletando:
                    coletando = True
                    hora_inicio = datetime.now()
                    janela.clear()
                    total_aceleracoes = 0
                    total_frenagens = 0
                    estado_evento = "neutro"
                    ultimo_comando_id_processado = comando.get("id")
                    print("\n🟢 PERCURSO INICIADO — coletando dados do sensor...\n")
                    enviar_estado(
                        {
                            "coletando": True,
                            "finalizado": False,
                            "total_aceleracoes": 0,
                            "total_frenagens": 0,
                            "amostras": [],
                        }
                    )

                elif acao == "finalizar" and coletando:
                    coletando = False
                    duracao_min = max((datetime.now() - hora_inicio).total_seconds() / 60, 0.01)
                    ultimo_comando_id_processado = comando.get("id")
                    total_eventos = total_aceleracoes + total_frenagens
                    amostras_coletadas = len(janela)

                    enviar_estado(
                        {
                            "coletando": False,
                            "finalizado": True,
                            "finalizado_em": datetime.now().isoformat(),
                            "total_aceleracoes": total_aceleracoes,
                            "total_frenagens": total_frenagens,
                            "duracao_min": round(duracao_min, 2),
                            "amostras": list(janela),
                        }
                    )

                    print("\n" + "=" * 50)
                    print("✅ PERCURSO FINALIZADO COM SUCESSO")
                    print("=" * 50)
                    print(f"  Data/hora:              {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
                    print(f"  Duração do percurso:    {duracao_min:.2f} min")
                    print(f"  Acelerações bruscas:    {total_aceleracoes}")
                    print(f"  Frenagens bruscas:      {total_frenagens}")
                    print(f"  Total de eventos:       {total_eventos}")
                    print(f"  Amostras coletadas:     {amostras_coletadas}")
                    print(f"  Dados enviados para:    {FIREBASE_URL}/estado")
                    print("=" * 50 + "\n")

        # 2. Lê a serial, só se estiver coletando de verdade
        if coletando:
            try:
                if conexao.in_waiting > 0:
                    linha = conexao.readline().decode("utf-8", errors="ignore").strip()
                    partes = linha.split(",")
                    if len(partes) == 2:
                        try:
                            accel_x = float(partes[0])
                            forca_g = float(partes[1])
                        except ValueError:
                            accel_x = forca_g = None

                        if accel_x is not None:
                            # detecção de evento com histerese
                            if accel_x >= LIMIAR_ACELERACAO:
                                if estado_evento != "acelerando":
                                    total_aceleracoes += 1
                                estado_evento = "acelerando"
                            elif accel_x <= -LIMIAR_FRENAGEM:
                                if estado_evento != "freando":
                                    total_frenagens += 1
                                estado_evento = "freando"
                            elif abs(accel_x) <= MARGEM_HISTERESE:
                                estado_evento = "neutro"

                            janela.append(
                                {
                                    "tempo": datetime.now().strftime("%H:%M:%S"),
                                    "accel_x": accel_x,
                                    "forca_g": forca_g,
                                }
                            )
            except Exception as erro:
                print(f"[erro na leitura serial] {erro}")

            # 3. Envia a telemetria pro Firebase periodicamente, enquanto coleta
            if agora - ultimo_envio_telemetria >= INTERVALO_ENVIO_TELEMETRIA_S:
                enviar_estado(
                    {
                        "coletando": True,
                        "finalizado": False,
                        "total_aceleracoes": total_aceleracoes,
                        "total_frenagens": total_frenagens,
                        "amostras": list(janela),
                    }
                )
                ultimo_envio_telemetria = agora
                print(f"Enviado: {total_aceleracoes} aceleração(ões), {total_frenagens} frenagem(ns)")

        time.sleep(0.05)

    conexao.close()
    print("\nPonte encerrada. Pode fechar esta janela.")


if __name__ == "__main__":
    main()
