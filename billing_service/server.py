
def obter_cid(context):
    try:
        md = dict(context.invocation_metadata())
        return md.get('x-correlation-id', 'req-local')
    except Exception:
        return 'req-local'

import os
import sys
import grpc
from concurrent import futures
import psycopg2

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(BASE_DIR)
sys.path.append(os.path.join(BASE_DIR, 'protos'))

import billing_pb2
import billing_pb2_grpc

TABELA_MAO_DE_OBRA = {
    "ELETRICA": 90.00,
    "IGNICAO": 140.00,
    "COMBUSTIVEL": 210.00,
    "PADRAO": 120.00
}

def get_db_connection():
    env_vars = {}
    env_file = os.path.join(BASE_DIR, '.env')
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    k, v = line.strip().split('=', 1)
                    env_vars[k.strip()] = v.strip().strip('"').strip("'")

    return psycopg2.connect(
        host=os.getenv('DB_HOST', env_vars.get('DB_HOST', 'localhost')),
        database=os.getenv('DB_NAME', env_vars.get('DB_NAME', 'clinica_db')),
        user=os.getenv('DB_USER', env_vars.get('DB_USER', 'postgres')),
        password=os.getenv('DB_PASS', env_vars.get('DB_PASS', '')),
        port=os.getenv('DB_PORT', env_vars.get('DB_PORT', '5432'))
    )

class BillingService(billing_pb2_grpc.BillingServiceServicer):

    def GerarFatura(self, request, context):
        cid = obter_cid(context)
        print(f'\033[95m[BillingService :50053]\033[0m \033[93m[{cid}]\033[0m Gerando fatura para OS #{request.diagnostico_id}')
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()

            # Se já existir fatura para essa OS, retorna
            cur.execute("""
                SELECT id, valor_peca, valor_mao_de_obra, valor_total, status_pagamento, forma_pagamento, 
                       to_char(data_faturamento, 'YYYY-MM-DD HH24:MI:SS'), COALESCE(desconto_aplicado, 0.00) 
                FROM faturas WHERE diagnostico_id = %s;
            """, (request.diagnostico_id,))
            row = cur.fetchone()

            if row:
                fat = billing_pb2.Fatura(
                    id=row[0], diagnostico_id=request.diagnostico_id,
                    valor_peca=float(row[1]), valor_mao_de_obra=float(row[2]),
                    valor_total=float(row[3]), status_pagamento=row[4],
                    forma_pagamento=row[5] or "", data_faturamento=str(row[6]),
                    desconto_aplicado=float(row[7])
                )
                cur.close()
                return billing_pb2.FaturaResponse(sucesso=True, mensagem="Fatura já existente recuperada.", fatura=fat)

            # Cálculo de mão de obra dinâmica pela categoria da peça
            cat = request.categoria_peca.strip().upper()
            if "ELETRIC" in cat or "SENSOR" in cat:
                mao_obra = TABELA_MAO_DE_OBRA["ELETRICA"]
            elif "IGNIC" in cat or "VELA" in cat or "BOBINA" in cat:
                mao_obra = TABELA_MAO_DE_OBRA["IGNICAO"]
            elif "COMBUSTIVEL" in cat or "BOMBA" in cat or "INJECAO" in cat:
                mao_obra = TABELA_MAO_DE_OBRA["COMBUSTIVEL"]
            else:
                mao_obra = TABELA_MAO_DE_OBRA["PADRAO"]

            total = round(request.valor_peca + mao_obra, 2)

            query = """
                INSERT INTO faturas (diagnostico_id, valor_peca, valor_mao_de_obra, valor_total, status_pagamento, desconto_aplicado)
                VALUES (%s, %s, %s, %s, 'PENDENTE', 0.00)
                RETURNING id, to_char(data_faturamento, 'YYYY-MM-DD HH24:MI:SS');
            """
            cur.execute(query, (request.diagnostico_id, request.valor_peca, mao_obra, total))
            fatura_id, dt = cur.fetchone()
            conn.commit()
            cur.close()

            fat = billing_pb2.Fatura(
                id=fatura_id, diagnostico_id=request.diagnostico_id,
                valor_peca=float(request.valor_peca), valor_mao_de_obra=float(mao_obra),
                valor_total=float(total), status_pagamento="PENDENTE",
                forma_pagamento="", data_faturamento=str(dt), desconto_aplicado=0.00
            )
            return billing_pb2.FaturaResponse(sucesso=True, mensagem="Fatura gerada com sucesso no BillingService.", fatura=fat)
        except Exception as e:
            if conn: conn.rollback()
            return billing_pb2.FaturaResponse(sucesso=False, mensagem=f"Erro no BillingService: {str(e)}")
        finally:
            if conn: conn.close()

    def ObterFaturaPorDiagnostico(self, request, context):
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT id, valor_peca, valor_mao_de_obra, valor_total, status_pagamento, forma_pagamento, 
                       to_char(data_faturamento, 'YYYY-MM-DD HH24:MI:SS'), COALESCE(desconto_aplicado, 0.00)
                FROM faturas WHERE diagnostico_id = %s;
            """, (request.diagnostico_id,))
            row = cur.fetchone()
            cur.close()

            if not row:
                return billing_pb2.FaturaResponse(sucesso=False, mensagem="Fatura não encontrada para esta OS.")

            fat = billing_pb2.Fatura(
                id=row[0], diagnostico_id=request.diagnostico_id,
                valor_peca=float(row[1]), valor_mao_de_obra=float(row[2]),
                valor_total=float(row[3]), status_pagamento=row[4],
                forma_pagamento=row[5] or "", data_faturamento=str(row[6]),
                desconto_aplicado=float(row[7])
            )
            return billing_pb2.FaturaResponse(sucesso=True, mensagem="Fatura encontrada.", fatura=fat)
        except Exception as e:
            return billing_pb2.FaturaResponse(sucesso=False, mensagem=str(e))
        finally:
            if conn: conn.close()

    # REGRA COMERCIAL: Desconto de 5% no PIX
    def PagarFatura(self, request, context):
        cid = obter_cid(context)
        print(f'\033[95m[BillingService :50053]\033[0m \033[93m[{cid}]\033[0m Fatura #{request.fatura_id} paga via {request.forma_pagamento}')
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT id, valor_peca, valor_mao_de_obra, valor_total, status_pagamento FROM faturas WHERE id = %s;", (request.fatura_id,))
            row = cur.fetchone()
            if not row:
                return billing_pb2.FaturaResponse(sucesso=False, mensagem="Fatura não encontrada para pagamento.")
            
            fat_id, v_peca, v_mao, v_total_atual, status_atual = row[0], float(row[1]), float(row[2]), float(row[3]), row[4]
            if status_atual == "PAGO":
                return billing_pb2.FaturaResponse(sucesso=False, mensagem="Esta fatura já foi quitada.")

            forma = request.forma_pagamento.strip().upper()
            subtotal = round(v_peca + v_mao, 2)
            desconto = 0.00
            
            if forma == "PIX":
                desconto = round(subtotal * 0.05, 2) # 5% de desconto no PIX
                valor_final = round(subtotal - desconto, 2)
                msg = f"Pagamento via PIX confirmado! Desconto de 5% (R$ {desconto:.2f}) aplicado com sucesso."
            else:
                valor_final = subtotal
                msg = f"Pagamento via {forma} confirmado no valor integral de R$ {valor_final:.2f}."

            cur.execute("""
                UPDATE faturas 
                SET status_pagamento = 'PAGO', forma_pagamento = %s, valor_total = %s, desconto_aplicado = %s
                WHERE id = %s
                RETURNING diagnostico_id, to_char(data_faturamento, 'YYYY-MM-DD HH24:MI:SS');
            """, (forma, valor_final, desconto, fat_id))
            diag_id, dt = cur.fetchone()
            cur.execute("UPDATE diagnosticos SET status = 'CONCLUIDO' WHERE id = %s;", (diag_id,))
            conn.commit()
            cur.close()

            fat = billing_pb2.Fatura(
                id=fat_id, diagnostico_id=diag_id,
                valor_peca=v_peca, valor_mao_de_obra=v_mao,
                valor_total=valor_final, status_pagamento="PAGO",
                forma_pagamento=forma, data_faturamento=str(dt),
                desconto_aplicado=desconto
            )
            return billing_pb2.FaturaResponse(sucesso=True, mensagem=msg, fatura=fat)
        except Exception as e:
            if conn: conn.rollback()
            return billing_pb2.FaturaResponse(sucesso=False, mensagem=str(e))
        finally:
            if conn: conn.close()

def serve():
    port = os.getenv('BILLING_PORT', '50053')
    server = futures.ThreadPoolExecutor(max_workers=10)
    grpc_server = grpc.server(server)
    billing_pb2_grpc.add_BillingServiceServicer_to_server(BillingService(), grpc_server)
    grpc_server.add_insecure_port(f'[::]:{port}')
    grpc_server.start()
    print(f"[*] BillingService (Faturamento + Desconto PIX) rodando na porta {port}...")
    grpc_server.wait_for_termination()

if __name__ == '__main__':
    serve()
