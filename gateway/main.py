
def obter_grpc_metadata():
    corr_id = f"req-{uuid.uuid4().hex[:6]}"
    return (('x-correlation-id', corr_id),), corr_id

import uuid
import os
import sys
import re
import grpc
from datetime import datetime, timedelta
from typing import Optional
import jwt

from fastapi import FastAPI, Depends, HTTPException, status, Request, Query
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, field_validator

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(BASE_DIR)
sys.path.append(os.path.join(BASE_DIR, 'protos'))

import catalog_pb2
import catalog_pb2_grpc
import diagnostic_pb2
import diagnostic_pb2_grpc
import billing_pb2
import billing_pb2_grpc

JWT_SECRET = os.getenv("JWT_SECRET", "autodiag_segredo_super_seguro_2026")
JWT_ALGORITHM = "HS256"
security_scheme = HTTPBearer(auto_error=False)

app = FastAPI(
    title="AutoDiag API Gateway",
    description="Gateway centralizador REST/JSON com tradução para 3 microsserviços gRPC",
    version="1.5.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CATALOG_HOST = os.getenv("CATALOG_HOST", "localhost:50051")
DIAGNOSTIC_HOST = os.getenv("DIAGNOSTIC_HOST", "localhost:50052")
BILLING_HOST = os.getenv("BILLING_HOST", "localhost:50053")

catalog_channel = grpc.insecure_channel(CATALOG_HOST)
catalog_stub = catalog_pb2_grpc.CatalogServiceStub(catalog_channel)

diagnostic_channel = grpc.insecure_channel(DIAGNOSTIC_HOST)
diagnostic_stub = diagnostic_pb2_grpc.DiagnosticServiceStub(diagnostic_channel)

billing_channel = grpc.insecure_channel(BILLING_HOST)
billing_stub = billing_pb2_grpc.BillingServiceStub(billing_channel)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    erros = []
    for err in exc.errors():
        campo = " -> ".join([str(loc) for loc in err.get("loc", []) if str(loc) != "body"])
        erros.append(f"{campo}: {err.get('msg')}")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"status": 400, "erro": "Bad Request", "mensagem": "Erro de validação semântica.", "detalhes": erros}
    )

def autenticar_jwt(credentials: HTTPAuthorizationCredentials = Depends(security_scheme)):
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Cabeçalho Authorization ausente.")
    try:
        return jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token JWT inválido ou expirado.")

class LoginDTO(BaseModel):
    usuario: str = Field(..., min_length=1)
    senha: str = Field(..., min_length=1)

class CriarDiagnosticoDTO(BaseModel):
    cliente_nome: str = Field(..., min_length=3, max_length=100)
    veiculo_id: int = Field(..., gt=0)
    peca_id: int = Field(..., gt=0)
    descricao_sintoma: str = Field(..., min_length=5, max_length=500)

    @field_validator('cliente_nome')
    @classmethod
    def validar_nome_cliente(cls, v: str) -> str:
        limpo = v.strip()
        if len(limpo) < 3:
            raise ValueError("O nome do cliente deve ter no mínimo 3 caracteres.")
        if re.search(r'[0-9]', limpo):
            raise ValueError("O nome do cliente não pode conter algarismos numéricos.")
        if not re.match(r"^[a-zA-ZÀ-ÿ\s'.]+$", limpo):
            raise ValueError("O nome do cliente contém caracteres especiais inválidos.")
        return limpo

    @field_validator('descricao_sintoma')
    @classmethod
    def validar_sintoma(cls, v: str) -> str:
        limpo = v.strip()
        if len(limpo) < 5:
            raise ValueError("A descrição deve ter ao menos 5 caracteres significativos.")
        return limpo

class PagarFaturaDTO(BaseModel):
    forma_pagamento: str = Field(..., min_length=3)

@app.get("/")
def renderizar_frontend():
    caminho = os.path.join(BASE_DIR, 'frontend', 'index.html')
    return FileResponse(caminho)

@app.post("/auth/login")
def login(dados: LoginDTO):
    if dados.usuario == "mecanico" and dados.senha == "123456":
        token = jwt.encode({"sub": dados.usuario, "role": "tecnico", "exp": datetime.utcnow() + timedelta(hours=4)}, JWT_SECRET, algorithm=JWT_ALGORITHM)
        return {"sucesso": True, "access_token": token, "token_type": "bearer"}
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas.")

@app.get("/api/v1/veiculos", dependencies=[Depends(autenticar_jwt)])
def listar_veiculos():
    try:
        resp = catalog_stub.ListarVeiculos(catalog_pb2.EmptyRequest(), timeout=8.0)
        return [{"id": v.id, "modelo": v.modelo, "montadora": v.montadora, "ano": v.ano} for v in resp.veiculos]
    except grpc.RpcError as e:
        raise HTTPException(status_code=503, detail=f"Erro gRPC: {e.details()}")

@app.get("/api/v1/pecas", dependencies=[Depends(autenticar_jwt)])
def listar_pecas():
    try:
        resp = catalog_stub.ListarPecas(catalog_pb2.EmptyRequest(), timeout=8.0)
        return [{
            "id": p.id, "nome": p.nome, "categoria": p.categoria,
            "codigo_referencia": p.codigo_referencia, "preco": p.preco,
            "quantidade_estoque": p.quantidade_estoque
        } for p in resp.pecas]
    except grpc.RpcError as e:
        raise HTTPException(status_code=503, detail=f"Erro gRPC: {e.details()}")

@app.post("/api/v1/diagnosticos", status_code=status.HTTP_201_CREATED, dependencies=[Depends(autenticar_jwt)])
def criar_diagnostico(dto: CriarDiagnosticoDTO):
    try:
        req = diagnostic_pb2.CriarDiagnosticoRequest(
            cliente_nome=dto.cliente_nome, veiculo_id=dto.veiculo_id,
            peca_id=dto.peca_id, descricao_sintoma=dto.descricao_sintoma
        )
        resp = diagnostic_stub.CriarDiagnostico(req, timeout=8.0)
        if not resp.sucesso:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=resp.mensagem)
        d = resp.diagnostico
        return {
            "status": 201, "mensagem": resp.mensagem,
            "dados": {
                "id": d.id, "cliente_nome": d.cliente_nome, "veiculo_id": d.veiculo_id,
                "veiculo_modelo": d.veiculo_modelo, "peca_id": d.peca_id, "peca_nome": d.peca_nome,
                "descricao_sintoma": d.descricao_sintoma, "status": d.status, "data_criacao": d.data_criacao
            }
        }
    except grpc.RpcError as e:
        raise HTTPException(status_code=503, detail=f"Erro gRPC: {e.details()}")

@app.get("/api/v1/diagnosticos", dependencies=[Depends(autenticar_jwt)])
def listar_diagnosticos(status: Optional[str] = Query(None)):
    try:
        req = diagnostic_pb2.ListarDiagnosticosRequest(status_filtro=status or "")
        resp = diagnostic_stub.ListarDiagnosticos(req, timeout=8.0)
        return [{
            "id": d.id, "cliente_nome": d.cliente_nome, "veiculo_id": d.veiculo_id,
            "veiculo_modelo": d.veiculo_modelo, "peca_id": d.peca_id, "peca_nome": d.peca_nome,
            "descricao_sintoma": d.descricao_sintoma, "status": d.status, "data_criacao": d.data_criacao
        } for d in resp.diagnosticos]
    except grpc.RpcError as e:
        raise HTTPException(status_code=503, detail=f"Erro gRPC: {e.details()}")

@app.put("/api/v1/diagnosticos/{id}/concluir", dependencies=[Depends(autenticar_jwt)])
def concluir_diagnostico(id: int):
    try:
        req = diagnostic_pb2.ConcluirDiagnosticoRequest(diagnostico_id=id)
        resp = diagnostic_stub.ConcluirDiagnostico(req, timeout=8.0)
        if not resp.sucesso:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=resp.mensagem)
        return {"status": 200, "mensagem": resp.mensagem}
    except grpc.RpcError as e:
        raise HTTPException(status_code=503, detail=f"Erro gRPC: {e.details()}")

# --- ROTAS DO NOVO BillingService (Faturamento) ---
@app.get("/api/v1/faturas/{diagnostico_id}", dependencies=[Depends(autenticar_jwt)])
def obter_fatura(diagnostico_id: int):
    try:
        req = billing_pb2.ObterFaturaRequest(diagnostico_id=diagnostico_id)
        resp = billing_stub.ObterFaturaPorDiagnostico(req, timeout=8.0)
        if not resp.sucesso:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=resp.mensagem)
        f = resp.fatura
        return {
            "id": f.id, "diagnostico_id": f.diagnostico_id,
            "valor_peca": f.valor_peca, "valor_mao_de_obra": f.valor_mao_de_obra,
            "valor_total": f.valor_total, "status_pagamento": f.status_pagamento,
            "forma_pagamento": f.forma_pagamento, "data_faturamento": f.data_faturamento, "desconto_aplicado": f.desconto_aplicado
        }
    except grpc.RpcError as e:
        raise HTTPException(status_code=503, detail=f"Erro gRPC no BillingService: {e.details()}")

@app.post("/api/v1/faturas/{fatura_id}/pagar", dependencies=[Depends(autenticar_jwt)])
def pagar_fatura(fatura_id: int, dto: PagarFaturaDTO):
    try:
        req = billing_pb2.PagarFaturaRequest(fatura_id=fatura_id, forma_pagamento=dto.forma_pagamento)
        resp = billing_stub.PagarFatura(req, timeout=8.0)
        if not resp.sucesso:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=resp.mensagem)
        return {"status": 200, "mensagem": resp.mensagem}
    except grpc.RpcError as e:
        raise HTTPException(status_code=503, detail=f"Erro gRPC no BillingService: {e.details()}")


class AjusteEstoquePayload(BaseModel):
    quantidade: int
    tipo: str # "ENTRADA" ou "SAIDA"

@app.post("/api/v1/pecas/{peca_id}/ajustar", dependencies=[Depends(autenticar_jwt)])
def api_ajustar_estoque(peca_id: int, payload: AjusteEstoquePayload):
    if payload.quantidade <= 0:
        raise HTTPException(status_code=400, detail="A quantidade deve ser maior que zero.")
    if payload.tipo.upper() not in ["ENTRADA", "SAIDA"]:
        raise HTTPException(status_code=400, detail="Tipo inválido. Selecione ENTRADA ou SAIDA.")
    
    metadata, cid = obter_grpc_metadata()
    try:
        req = catalog_pb2.AjustarEstoqueRequest(
            peca_id=peca_id,
            quantidade=payload.quantidade,
            tipo=payload.tipo.upper()
        )
        resp = catalog_stub.AjustarEstoque(req, metadata=metadata, timeout=8.0)
        if not resp.sucesso:
            status_erro = 409 if "insuficiente" in resp.mensagem.lower() else 400
            raise HTTPException(status_code=status_erro, detail=resp.mensagem)
        return {"sucesso": True, "mensagem": resp.mensagem, "novo_estoque": resp.novo_estoque}
    except grpc.RpcError as e:
        raise HTTPException(status_code=503, detail="CatalogService indisponível.")
