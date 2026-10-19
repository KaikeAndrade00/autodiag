#!/bin/bash
cd ~/autodiag
source venv/bin/activate

echo "[*] Encerrando processos anteriores..."
pkill -f "python.*server.py" || true
pkill -f "uvicorn.*gateway.main:app" || true
sleep 1

echo "[1/4] Iniciando CatalogService (gRPC :50051)..."
nohup python3 catalog_service/server.py > catalog.log 2>&1 &

echo "[2/4] Iniciando BillingService (gRPC :50053)..."
nohup python3 billing_service/server.py > billing.log 2>&1 &

echo "[3/4] Iniciando DiagnosticService (gRPC :50052)..."
nohup python3 diagnostic_service/server.py > diagnostic.log 2>&1 &

sleep 2

echo "[4/4] Iniciando API Gateway (FastAPI/Uvicorn :8000)..."
nohup uvicorn gateway.main:app --host 0.0.0.0 --port 8000 > gateway.log 2>&1 &

sleep 2
echo ""
echo "=========================================================="
echo "  ECOSSISTEMA DISTRIBUÍDO NO AR COM 3 MICROSSERVIÇOS!"
echo "  - CatalogService:     localhost:50051 (gRPC)"
echo "  - DiagnosticService:  localhost:50052 (gRPC)"
echo "  - BillingService:     localhost:50053 (gRPC)"
echo "  - API Gateway:        http://0.0.0.0:8000 (REST + JWT)"
echo "=========================================================="
