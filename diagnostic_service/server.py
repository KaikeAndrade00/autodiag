
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

import diagnostic_pb2
import diagnostic_pb2_grpc
import catalog_pb2
import catalog_pb2_grpc
import billing_pb2
import billing_pb2_grpc

CATALOG_HOST = os.getenv("CATALOG_HOST", "localhost:50051")
catalog_channel = grpc.insecure_channel(CATALOG_HOST)
catalog_stub = catalog_pb2_grpc.CatalogServiceStub(catalog_channel)

BILLING_HOST = os.getenv("BILLING_HOST", "localhost:50053")
billing_channel = grpc.insecure_channel(BILLING_HOST)
billing_stub = billing_pb2_grpc.BillingServiceStub(billing_channel)

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

class DiagnosticService(diagnostic_pb2_grpc.DiagnosticServiceServicer):
    
    def CriarDiagnostico(self, request, context):
        conn = None
        try:
            resp_veic = catalog_stub.ObterVeiculoPorId(catalog_pb2.IdRequest(id=request.veiculo_id), timeout=3)
            if not resp_veic.encontrado:
                return diagnostic_pb2.DiagnosticoResponse(sucesso=False, mensagem=f"Veículo ID {request.veiculo_id} inexistente.")
            
            resp_peca = catalog_stub.ObterPecaPorId(catalog_pb2.IdRequest(id=request.peca_id), timeout=3)
            if not resp_peca.encontrado:
                return diagnostic_pb2.DiagnosticoResponse(sucesso=False, mensagem=f"Peça ID {request.peca_id} inexistente.")

            conn = get_db_connection()
            cur = conn.cursor()
            query = """
                INSERT INTO diagnosticos (cliente_nome, veiculo_id, peca_id, descricao_sintoma, status)
                VALUES (%s, %s, %s, %s, 'EM_ANALISE')
                RETURNING id, data_criacao, status;
            """
            cur.execute(query, (request.cliente_nome, request.veiculo_id, request.peca_id, request.descricao_sintoma))
            diag_id, data_criacao, status = cur.fetchone()
            conn.commit()
            cur.close()

            d = diagnostic_pb2.Diagnostico(
                id=diag_id, cliente_nome=request.cliente_nome, veiculo_id=request.veiculo_id,
                veiculo_modelo=resp_veic.veiculo.modelo, peca_id=request.peca_id,
                peca_nome=resp_peca.peca.nome, descricao_sintoma=request.descricao_sintoma,
                status=status, data_criacao=str(data_criacao)
            )
            return diagnostic_pb2.DiagnosticoResponse(sucesso=True, mensagem="Ordem de diagnóstico criada com sucesso.", diagnostico=d)
        except Exception as e:
            return diagnostic_pb2.DiagnosticoResponse(sucesso=False, mensagem=f"Erro: {str(e)}")
        finally:
            if conn: conn.close()

    def ListarDiagnosticos(self, request, context):
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            status_filtro = request.status_filtro.strip().upper()
            
            if status_filtro in ["EM_ANALISE", "CONCLUIDO"]:
                query = """
                    SELECT d.id, d.cliente_nome, d.veiculo_id, v.modelo,
                           d.peca_id, p.nome, d.descricao_sintoma, d.status,
                           to_char(d.data_criacao, 'YYYY-MM-DD HH24:MI:SS')
                    FROM diagnosticos d
                    JOIN veiculos v ON d.veiculo_id = v.id
                    JOIN pecas p ON d.peca_id = p.id
                    WHERE d.status = %s
                    ORDER BY d.id DESC;
                """
                cur.execute(query, (status_filtro,))
            else:
                query = """
                    SELECT d.id, d.cliente_nome, d.veiculo_id, v.modelo,
                           d.peca_id, p.nome, d.descricao_sintoma, d.status,
                           to_char(d.data_criacao, 'YYYY-MM-DD HH24:MI:SS')
                    FROM diagnosticos d
                    JOIN veiculos v ON d.veiculo_id = v.id
                    JOIN pecas p ON d.peca_id = p.id
                    ORDER BY d.id DESC;
                """
                cur.execute(query)

            rows = cur.fetchall()
            cur.close()

            diagnosticos = [diagnostic_pb2.Diagnostico(
                id=r[0], cliente_nome=r[1], veiculo_id=r[2], veiculo_modelo=r[3],
                peca_id=r[4], peca_nome=r[5], descricao_sintoma=r[6], status=r[7], data_criacao=r[8]
            ) for r in rows]
            return diagnostic_pb2.ListaDiagnosticosResponse(diagnosticos=diagnosticos)
        finally:
            if conn: conn.close()

    def ConcluirDiagnostico(self, request, context):
        cid = obter_cid(context)
        md = (('x-correlation-id', cid),)
        print(f'\033[92m[DiagnosticService :50052]\033[0m \033[93m[{cid}]\033[0m Iniciando Saga/Orquestração da OS #{request.diagnostico_id}')
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT id, peca_id, status FROM diagnosticos WHERE id = %s FOR UPDATE;", (request.diagnostico_id,))
            row = cur.fetchone()
            if not row:
                return diagnostic_pb2.DiagnosticoResponse(sucesso=False, mensagem="Ordem de diagnóstico não encontrada.")
            
            diag_id, peca_id, status_atual = row[0], row[1], row[2]
            if status_atual == "CONCLUIDO":
                return diagnostic_pb2.DiagnosticoResponse(sucesso=False, mensagem="Esta ordem já foi concluída anteriormente.")

            # 1. Consulta peça e sua categoria no CatalogService
            resp_peca = catalog_stub.ObterPecaPorId(catalog_pb2.IdRequest(id=peca_id), timeout=3)
            preco_peca = resp_peca.peca.preco if resp_peca.encontrado else 150.00
            categoria_peca = resp_peca.peca.categoria if resp_peca.encontrado else "GERAL"

            # 2. Executa baixa física da peça
            resp_estoque = catalog_stub.BaixarEstoque(
                catalog_pb2.BaixarEstoqueRequest(peca_id=peca_id, quantidade=1), timeout=4
            )
            if not resp_estoque.sucesso:
                return diagnostic_pb2.DiagnosticoResponse(sucesso=False, mensagem=resp_estoque.mensagem)

            # 3. Dispara BillingService com categoria para cálculo de mão de obra dinâmica
            resp_fatura = billing_stub.GerarFatura(
                billing_pb2.GerarFaturaRequest(
                    diagnostico_id=diag_id,
                    valor_peca=preco_peca,
                    categoria_peca=categoria_peca
                ), timeout=4
            )
            if not resp_fatura.sucesso:
                return diagnostic_pb2.DiagnosticoResponse(
                    sucesso=False, mensagem=f"Falha ao gerar fatura no BillingService: {resp_fatura.mensagem}"
                )

            # 4. Atualiza ordem no Cloud SQL
            cur.execute(
                "UPDATE diagnosticos SET status = 'CONCLUIDO' WHERE id = %s RETURNING cliente_nome, veiculo_id, descricao_sintoma, data_criacao;",
                (diag_id,)
            )
            c_nome, v_id, desc, dt = cur.fetchone()
            conn.commit()
            cur.close()

            d = diagnostic_pb2.Diagnostico(
                id=diag_id, cliente_nome=c_nome, veiculo_id=v_id, peca_id=peca_id,
                descricao_sintoma=desc, status="CONCLUIDO", data_criacao=str(dt)
            )
            return diagnostic_pb2.DiagnosticoResponse(
                sucesso=True,
                mensagem=f"Reparo concluído! Fatura #{resp_fatura.fatura.id} gerada: R$ {resp_fatura.fatura.valor_total:.2f} (Mão de obra: R$ {resp_fatura.fatura.valor_mao_de_obra:.2f}).",
                diagnostico=d
            )
        except Exception as e:
            if conn: conn.rollback()
            return diagnostic_pb2.DiagnosticoResponse(sucesso=False, mensagem=f"Falha na transação distribuída: {str(e)}")
        finally:
            if conn: conn.close()

def serve():
    port = os.getenv('DIAGNOSTIC_PORT', '50052')
    server = futures.ThreadPoolExecutor(max_workers=10)
    grpc_server = grpc.server(server)
    diagnostic_pb2_grpc.add_DiagnosticServiceServicer_to_server(DiagnosticService(), grpc_server)
    grpc_server.add_insecure_port(f'[::]:{port}')
    grpc_server.start()
    print(f"[*] DiagnosticService rodando na porta {port}...")
    grpc_server.wait_for_termination()

if __name__ == '__main__':
    serve()
