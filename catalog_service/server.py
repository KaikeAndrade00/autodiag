
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

import catalog_pb2
import catalog_pb2_grpc

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

class CatalogService(catalog_pb2_grpc.CatalogServiceServicer):
    
    def ListarVeiculos(self, request, context):
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT id, modelo, montadora, ano FROM veiculos ORDER BY id ASC;")
            rows = cur.fetchall()
            cur.close()
            veiculos = [catalog_pb2.Veiculo(id=r[0], modelo=r[1], montadora=r[2], ano=r[3]) for r in rows]
            return catalog_pb2.ListaVeiculosResponse(veiculos=veiculos)
        finally:
            if conn: conn.close()

    def ListarPecas(self, request, context):
        cid = obter_cid(context)
        print(f'\033[94m[CatalogService :50051]\033[0m \033[93m[{cid}]\033[0m Listando catálogo de peças')
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT id, nome, categoria, codigo_referencia, preco, quantidade_estoque FROM pecas ORDER BY id ASC;")
            rows = cur.fetchall()
            cur.close()
            pecas = [catalog_pb2.Peca(
                id=r[0], nome=r[1], categoria=r[2], codigo_referencia=r[3],
                preco=float(r[4] or 0), quantidade_estoque=int(r[5] or 0)
            ) for r in rows]
            return catalog_pb2.ListaPecasResponse(pecas=pecas)
        finally:
            if conn: conn.close()

    def ObterVeiculoPorId(self, request, context):
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT id, modelo, montadora, ano FROM veiculos WHERE id = %s;", (request.id,))
            row = cur.fetchone()
            cur.close()
            if row:
                v = catalog_pb2.Veiculo(id=row[0], modelo=row[1], montadora=row[2], ano=row[3])
                return catalog_pb2.VeiculoResponse(encontrado=True, veiculo=v)
            return catalog_pb2.VeiculoResponse(encontrado=False)
        finally:
            if conn: conn.close()

    def ObterPecaPorId(self, request, context):
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT id, nome, categoria, codigo_referencia, preco, quantidade_estoque FROM pecas WHERE id = %s;", (request.id,))
            row = cur.fetchone()
            cur.close()
            if row:
                p = catalog_pb2.Peca(
                    id=row[0], nome=row[1], categoria=row[2], codigo_referencia=row[3],
                    preco=float(row[4] or 0), quantidade_estoque=int(row[5] or 0)
                )
                return catalog_pb2.PecaResponse(encontrado=True, peca=p)
            return catalog_pb2.PecaResponse(encontrado=False)
        finally:
            if conn: conn.close()

    # NOVO: Operação de Alteração (UPDATE no Banco Real)
    
    def AjustarEstoque(self, request, context):
        cid = obter_cid(context)
        peca_id = request.peca_id
        qtd = request.quantidade
        tipo = request.tipo.upper()
        print(f'\033[94m[CatalogService :50051]\033[0m \033[93m[{cid}]\033[0m Ajuste de estoque: Peça #{peca_id} | {tipo} de {qtd} un')

        if qtd <= 0:
            return catalog_pb2.AjustarEstoqueResponse(sucesso=False, mensagem="A quantidade deve ser maior que zero.", novo_estoque=0)

        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            
            # Detectar nome da coluna de estoque dinamicamente
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'pecas' AND column_name IN ('quantidade_estoque', 'quantidade');")
            col = cur.fetchone()[0]

            if tipo == "SAIDA":
                cur.execute(f"SELECT {col} FROM pecas WHERE id = %s FOR UPDATE;", (peca_id,))
                row = cur.fetchone()
                if not row:
                    return catalog_pb2.AjustarEstoqueResponse(sucesso=False, mensagem="Peça não encontrada.", novo_estoque=0)
                saldo_atual = row[0]
                if saldo_atual < qtd:
                    return catalog_pb2.AjustarEstoqueResponse(sucesso=False, mensagem=f"Saldo insuficiente. Disponível: {saldo_atual} un.", novo_estoque=saldo_atual)
                
                cur.execute(f"UPDATE pecas SET {col} = {col} - %s WHERE id = %s RETURNING {col};", (qtd, peca_id))
            elif tipo == "ENTRADA":
                cur.execute(f"UPDATE pecas SET {col} = {col} + %s WHERE id = %s RETURNING {col};", (qtd, peca_id))
            else:
                return catalog_pb2.AjustarEstoqueResponse(sucesso=False, mensagem="Tipo inválido. Use ENTRADA ou SAIDA.", novo_estoque=0)

            novo_saldo = cur.fetchone()[0]
            conn.commit()
            return catalog_pb2.AjustarEstoqueResponse(sucesso=True, mensagem=f"Estoque atualizado! Novo saldo: {novo_saldo} un.", novo_estoque=novo_saldo)
        except Exception as e:
            if conn: conn.rollback()
            print(f"[ERRO] AjustarEstoque: {e}")
            return catalog_pb2.AjustarEstoqueResponse(sucesso=False, mensagem=f"Erro interno: {str(e)}", novo_estoque=0)
        finally:
            if conn: conn.close()

    def BaixarEstoque(self, request, context):
        cid = obter_cid(context)
        print(f'\033[94m[CatalogService :50051]\033[0m \033[93m[{cid}]\033[0m Baixando {request.quantidade} un da peça #{request.peca_id}')
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT nome, quantidade_estoque FROM pecas WHERE id = %s FOR UPDATE;", (request.peca_id,))
            row = cur.fetchone()
            if not row:
                return catalog_pb2.BaixarEstoqueResponse(sucesso=False, mensagem="Peça não encontrada no catálogo.")
            
            nome_peca, estoque_atual = row[0], row[1]
            if estoque_atual < request.quantidade:
                return catalog_pb2.BaixarEstoqueResponse(
                    sucesso=False,
                    mensagem=f"Estoque insuficiente para a peça '{nome_peca}'. Disponível: {estoque_atual}, Solicitado: {request.quantidade}."
                )

            # Executa a alteração no banco real
            cur.execute(
                "UPDATE pecas SET quantidade_estoque = quantidade_estoque - %s WHERE id = %s RETURNING quantidade_estoque;",
                (request.quantidade, request.peca_id)
            )
            novo_estoque = cur.fetchone()[0]
            conn.commit()
            cur.close()
            return catalog_pb2.BaixarEstoqueResponse(sucesso=True, mensagem="Estoque atualizado com sucesso.", novo_estoque=novo_estoque)
        except Exception as e:
            if conn: conn.rollback()
            return catalog_pb2.BaixarEstoqueResponse(sucesso=False, mensagem=f"Erro de banco: {str(e)}")
        finally:
            if conn: conn.close()

def serve():
    port = os.getenv('CATALOG_PORT', '50051')
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    catalog_pb2_grpc.add_CatalogServiceServicer_to_server(CatalogService(), server)
    server.add_insecure_port(f'[::]:{port}')
    server.start()
    print(f"[*] CatalogService rodando na porta {port}...")
    server.wait_for_termination()

if __name__ == '__main__':
    serve()
