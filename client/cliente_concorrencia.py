import requests
import time
import concurrent.futures

# Endereço IP público do servidor AutoDiag
SERVER_IP = "34.41.180.75"
BASE_URL = f"http://{SERVER_IP}:8000"

def banner():
    print("\n" + "="*60)
    print("      AUTODIAG - CLIENTE DISTRIBUÍDO DE CONCORRÊNCIA       ")
    print("="*60)

def autenticar(usuario="admin", senha="123"):
    print(f"\n[*] [Auth] Autenticando nó cliente no Gateway ({BASE_URL})...")
    try:
        resp = requests.post(
            f"{BASE_URL}/api/v1/auth/login",
            json={"username": usuario, "password": senha},
            timeout=5
        )
        if resp.status_code == 200:
            token = resp.json().get("access_token")
            print("[✔] [Auth] Token JWT obtido com sucesso!")
            return token
        else:
            print(f"[✘] [Auth] Falha no login: HTTP {resp.status_code} - {resp.text}")
            return None
    except Exception as e:
        print(f"[✘] [Auth] Erro ao conectar ao servidor: {e}")
        return None

def consultar_catalogo(token):
    print("\n[*] [Catálogo] Requisitando peças via REST -> gRPC...")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(f"{BASE_URL}/api/v1/pecas", headers=headers, timeout=5)
        if resp.status_code == 200:
            pecas = resp.json()
            print(f"{'ID':<4} | {'Peça':<25} | {'Preço':<12} | {'Estoque':<10}")
            print("-" * 58)
            for p in pecas:
                print(f"{p['id']:<4} | {p['nome']:<25} | R$ {p['preco']:<9.2f} | {p['quantidade_estoque']} un")
            return pecas
        else:
            print(f"[✘] Falha ao listar peças: HTTP {resp.status_code}")
            return []
    except Exception as e:
        print(f"[✘] Erro de rede: {e}")
        return []

def disparar_liquidacao(token, os_id, thread_id):
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"metodo_pagamento": "PIX"}
    
    inicio = time.time()
    try:
        resp = requests.put(
            f"{BASE_URL}/api/v1/diagnosticos/{os_id}/concluir",
            json=payload,
            headers=headers,
            timeout=10
        )
        tempo = (time.time() - inicio) * 1000
        return {
            "thread": thread_id,
            "status_code": resp.status_code,
            "resposta": resp.json(),
            "tempo_ms": tempo
        }
    except Exception as e:
        return {
            "thread": thread_id,
            "status_code": 500,
            "resposta": {"detalhe": str(e)},
            "tempo_ms": (time.time() - inicio) * 1000
        }

def executar_teste_concorrencia(token):
    banner()
    pecas = consultar_catalogo(token)
    if not pecas:
        return

    print("\n" + "-"*60)
    os_id = input("[?] Digite o ID de uma Ordem 'EM ANÁLISE' para o teste de concorrência: ").strip()
    num_requisicoes = int(input("[?] Quantas requisições simultâneas disparar (ex: 5): ").strip() or "5")

    print(f"\n🚀 Disparando {num_requisicoes} requisições simultâneas para faturar a OS #{os_id}...")
    print("   (Simulando múltiplos operadores tentando liquidar o mesmo estoque)\n")

    resultados = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_requisicoes) as executor:
        futuros = [
            executor.submit(disparar_liquidacao, token, os_id, i+1)
            for i in range(num_requisicoes)
        ]
        for f in concurrent.futures.as_completed(futuros):
            resultados.append(f.result())

    print("\n" + "="*60)
    print("                    RESULTADOS DO TESTE                     ")
    print("="*60)
    print(f"{'Thread':<8} | {'Status HTTP':<14} | {'Latência':<10} | {'Resultado da Transação'}")
    print("-" * 60)

    sucessos = 0
    bloqueios = 0

    for r in sorted(resultados, key=lambda x: x["thread"]):
        st = r["status_code"]
        lat = f"{r['tempo_ms']:.1f}ms"
        
        if st == 200:
            sucessos += 1
            detalhe = "✔ SUCESSO (Fatura gerada e estoque debitado)"
        elif st == 400:
            bloqueios += 1
            detalhe = "🛡️ BLOQUEADO (Estoque zerado / OS já liquidada)"
        else:
            detalhe = f"Falha ({r['resposta']})"
            
        print(f"Req #{r['thread']:<4} | HTTP {st:<9} | {lat:<10} | {detalhe}")

    print("\n[Conclusão Distribuída]")
    print(f" • Transações Efetivadas: {sucessos} (esperado: no máximo 1)")
    print(f" • Transações Bloqueadas: {bloqueios} (isolamento de concorrência com sucesso)")
    print("="*60 + "\n")

if __name__ == "__main__":
    banner()
    usr = input("Usuário de acesso [admin]: ").strip() or "admin"
    pwd = input("Senha [123]: ").strip() or "123"
    
    token = autenticar(usr, pwd)
    if token:
        executar_teste_concorrencia(token)
