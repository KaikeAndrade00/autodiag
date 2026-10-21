# 🚗 AutoDiag — Sistema Distribuído de Gestão Automotiva

O **AutoDiag** é uma plataforma distribuída desenvolvida para a gestão integrada de serviços automotivos, controle de inventário de peças e liquidação financeira de Ordens de Serviço (OS)[cite: 3]. O projeto foi concebido sobre os pilares fundamentais da computação distribuída, resolvendo desafios de **consistência transacional estrita (ACID)**, **concorrência simultânea (Race Conditions)**, **idempotência de operações** e **comunicação heterogênea** em topologia de nuvem multi-instância no Google Cloud Platform (GCP)[cite: 3].

---

## 📌 Sumário

1. [Visão Geral e Propósito](#-visão-geral-e-propósito)
2. [Arquitetura Geral do Sistema](#-arquitetura-geral-do-sistema)
3. [Especificação dos Componentes e Microsserviços](#-especificação-dos-componentes-e-microsserviços)
4. [Ciclo de Vida da Ordem de Serviço](#-ciclo-de-vida-da-ordem-de-serviço)
5. [Controle de Concorrência e Resiliência Transacional](#-controle-de-concorrência-e-resiliência-transacional)
6. [Topologia de Nuvem Multi-VM (GCP)](#-topologia-de-nuvem-multi-vm-gcp)
7. [Estrutura do Repositório](#-estrutura-do-repositório)
8. [Configuração de Ambiente](#-configuração-de-ambiente)
9. [Guia de Instalação e Execução](#-guia-de-instalação-e-execução)
10. [Endpoints da API REST (Gateway)](#-endpoints-da-api-rest-gateway)
11. [Contratos de Interface (Protocol Buffers)](#-contratos-de-interface-protocol-buffers)
12. [Teste de Concorrência e Validação Distribuída](#-teste-de-concorrência-e-validação-distribuída)

---

## 🎯 Visão Geral e Propósito

Oficinas mecânicas e centros automotivos operam sob cenários críticos de negócio: orçamentos simultâneos disputando peças escassas em estoque, pagamentos processados em balcões concorrentes e necessidade de rastreabilidade de ponta a ponta.

O AutoDiag foi construído como um ecossistema distribuído que desacopla responsabilidades em serviços especializados, substituindo monólitos tradicionais por uma malha de **microsserviços gRPC** coordenados por um **API Gateway**, consumindo banco de dados relacional gerenciado e garantindo proteção contra vendas duplicadas ou inconsistências contábeis.
---

## 🏛️ Arquitetura Geral do Sistema

O ecossistema combina dois modelos de comunicação complementares[cite: 3]:
* **Borda Externa (Client-to-Gateway):** Protocolo HTTP/1.1 com payloads em formato JSON e autenticação via tokens JWT (*JSON Web Tokens*)[cite: 3].
* **Malha Interna (Service-to-Service):** Protocolo gRPC sobre HTTP/2 com serialização binária via *Protocol Buffers* (Protobuf), garantindo multiplexação de chamadas, tipagem rígida e baixa latência[cite: 3].

```text
  [ Cliente Web (Navegador) ]             [ Nó Cliente de Carga (VM 2) ]
              │                                          │
              │ HTTP/REST (Porta 8000)                   │ HTTP/REST (Porta 8000)
              ▼                                          ▼
   ┌─────────────────────────────────────────────────────────────┐
   │                FastAPI API Gateway (VM 1)                   │
   │  - Autenticação & Autorização Stateless (JWT)               │
   │  - Validação de Entrada & Rastreabilidade (Correlation ID)  │
   │  - Tradução de Protocolos (HTTP/JSON <-> gRPC/Protobuf)     │
   │  - Servidor de Arquivos Estáticos (Frontend SPA)            │
   └──────┬───────────────────────┬───────────────────────┬──────┘
          │ gRPC (:50051)         │ gRPC (:50052)         │ gRPC (:50053)
          ▼                       ▼                       ▼
   ┌───────────────┐       ┌───────────────┐       ┌───────────────┐
   │ CatalogSvc    │       │ DiagnosticSvc │       │  BillingSvc   │
   │               │       │ (Orquestrador)│       │               │
   │ - Veículos    │       │ - Abertura OS │       │ - Mão de Obra │
   │ - Estoque     │       │ - Fechamento  │       │ - Descontos   │
   │ - Almoxarif.  │       │ - Coordenação │       │ - Faturamento │
   └──────┬────────┘       └──────┬────────┘       └──────┬────────┘
          │                       │                       │
          └───────────────────────┼───────────────────────┘
                                  ▼
                   ┌─────────────────────────────┐
                   │   GCP Cloud SQL (Postgres)  │
                   │  - Trava Pessimista (Lock)  │
                   │  - Garantias ACID           │
                   └─────────────────────────────┘
```
---

## ⚙️ Especificação dos Componentes e Microsserviços

### 1. API Gateway (`gateway/main.py` | Porta `:8000`)
Atua como o ponto de entrada único (*Single Point of Contact*) para todos os clientes externos[cite: 3]:
* **Segurança na Borda:** Interceta requisições protegidas verificando a assinatura e a expiração do token JWT emitido na rota `/auth/login`[cite: 3].
* **Tradução de Protocolos:** Converte chamadas HTTP REST (JSON) em requisições gRPC binárias para os nós internos via canais gRPC dedicados (`insecure_channel`)[cite: 3].
* **Injeção de Rastreabilidade:** Gera ou propaga o cabeçalho `x-correlation-id` nos metadados gRPC, permitindo rastrear uma transação por todos os serviços do cluster.
* **Hospedagem Web:** Serve a interface gráfica estática (`frontend/index.html`) e a documentação interativa OpenAPI/Swagger (`/docs`).

### 2. Catalog Service (`catalog_service/server.py` | Porta `:50051`)
Responsável pela integridade e governança das peças e frotas de veículos[cite: 3]:
* Fornece a listagem de modelos automotivos homologados para manutenção.
* Controla as quantidades disponíveis no almoxarifado de peças de reposição[cite: 3].
* Realiza a baixa física de unidades sob demanda do serviço de diagnóstico (`BaixarEstoque`), garantindo que a quantidade nunca se torne negativa (`quantidade_estoque >= 0`)[cite: 3].
* Permite reposição e ajuste manual de inventário pelo operador da oficina.

### 3. Diagnostic Service (`diagnostic_service/server.py` | Porta `:50052`)
Atua como o **orquestrador transacional** do ciclo de vida das Ordens de Serviço[cite: 3]:
* Regista novas OS com validação de vínculo entre cliente, veículo e peça a reparar.
* Implementa o fluxo de conclusão: antes de liquidar o serviço, contacta o `CatalogService` para checagem de preço/peça, solicita a baixa do estoque físico e aciona o `BillingService` para apuração dos custos.
* Executa o controlo de concorrência com bloqueio atómico de linha para impedir liquidações redundantes[cite: 3].

### 4. Billing Service (`billing_service/server.py` | Porta `:50053`)
Centraliza as regras de tarifação financeira e emissão contábil[cite: 3]:
* Calcula o custo dinâmico de mão de obra conforme a complexidade da categoria técnica da peça (`MOTOR`, `CAMBIO`, `FREIO`, `ELETRICA`, `SUSPENSAO` ou `GERAL`)[cite: 3].
* Consolida o valor total (`Peça + Mão de Obra`).
* Aplica tabelas de desconto por modalidade de pagamento (ex.: abatimento de 10% para pagamentos via PIX ou dinheiro em espécie)[cite: 3].
* Persiste a fatura oficial associada à respetiva Ordem de Serviço na tabela `faturas`.

### 5. Frontend Operacional (`frontend/index.html`)
Painel de controlo visual em modo escuro (*Dark Mode*), estilizado com Tailwind CSS e JavaScript:
* **Feedback Imediato de Sessão:** Indicador luminoso de validação do token JWT.
* **Triagem Preventiva (*Fail-Early*):** Deteta itens zerados no almoxarifado diretamente no menu seletor, impedindo a abertura de OS sem cobertura de peças[cite: 3].
* **Monitor de Ordens e Almoxarifado:** Exibe o estado em tempo real de cada veículo em manutenção e cartões de inventário com ajuste rápido de saldo.
* **Modal de Pagamento e Quitação:** Interface para escolha do método de pagamento (PIX, Dinheiro, Cartão) e exibição do recibo detalhado com o desconto concedido.

### 6. Banco de Dados Relacional Gerenciado (GCP Cloud SQL | Porta `:5432`)
Instância gerenciada de **PostgreSQL 17** em nuvem, garantindo[cite: 3]:
* Isolamento de transações e durabilidade (ACID)[cite: 3].
* Suporte nativo a bloqueios explícitos por linha (`FOR UPDATE`)[cite: 3].
* Integridade referencial entre veículos, diagnósticos, peças e faturas.

  ---

## 🔄 Ciclo de Vida da Ordem de Serviço

```text
   [ Cliente / Operador ]
             │
             │ 1. Criação da OS (POST /api/v1/diagnosticos)
             ▼
   ┌───────────────────┐
   │    EM ANÁLISE     │ ◄─── Registo gravado na base de dados; veículo sob vistoria.
   └─────────┬─────────┘
             │
             │ 2. Faturação / Pagamento (PUT /api/v1/diagnosticos/{id}/concluir)
             ▼
   ┌───────────────────┐
   │  Bloqueio Linha   │ ◄─── SELECT ... FOR UPDATE (Exclusão mútua no PostgreSQL)
   └─────────┬─────────┘
             ├─────────────────────────────────────────┐
             │ Status == 'EM_ANALISE'                  │ Status == 'CONCLUIDO'
             ▼                                         ▼
   ┌───────────────────┐                     ┌───────────────────┐
   │ Baixa no Estoque  │                     │   HTTP 400 Bad    │
   │ (CatalogService)  │                     │     Request       │
   └─────────┬─────────┘                     │  (OS Já Liquidada)│
             │                               └───────────────────┘
             ▼
   ┌───────────────────┐
   │ Emissão de Fatura │
   │ (BillingService)  │
   └─────────┬─────────┘
             │
             ▼
   ┌───────────────────┐
   │     CONCLUÍDO     │ ◄─── OS liquidada, peça debitada e fatura emitida.
   └───────────────────┘
```

---

## 🛡️ Controlo de Concorrência e Resiliência Transacional

Em sistemas distribuídos sem coordenação, requisições paralelas podem incorrer em condições de corrida (*Race Conditions*) do tipo **Time-of-Check to Time-of-Use (TOCTOU)**: múltiplos processos leem simultaneamente que um registo está aberto e disparam efeitos colaterais de forma redundante.

O AutoDiag neutraliza esse risco através de três camadas defensivas:

### 1. Prevenção Antecipada (*Fail-Early*)
O frontend e o Gateway realizam a verificação do saldo de peças antes de submeter requisições à malha interna[cite: 3]. Peças com 0 unidades em inventário são sinalizadas e bloqueadas preventivamente, reduzindo tráfego desnecessário na rede gRPC[cite: 3].

### 2. Bloqueio Pessimista de Linha (`SELECT ... FOR UPDATE`)
A liquidação das ordens utiliza **bloqueio pessimista de linha (`SELECT ... FOR UPDATE`)** no PostgreSQL[cite: 3]:
```sql
SELECT id, peca_id, status FROM diagnosticos WHERE id = %s FOR UPDATE;
```
* A primeira requisição que alcança o registo obtém o bloqueio exclusivo da linha no PostgreSQL[cite: 3].
* As requisições concorrentes subsequentes ficam retidas na fila do gestor de base de dados[cite: 3].
* Assim que a primeira transação debita o estoque, emite a fatura e atualiza o estado para `CONCLUIDO`, as requisições retidas são libertadas e rejeitadas pelo novo estado já consolidado[cite: 3].

### 3. Idempotência Transacional
A transição de estado da ordem é unívoca e atómica[cite: 3]. Uma vez concluída, chamadas repetidas retornam erro amigável sem provocar novos débitos ou emissão duplicada de faturas[cite: 3].

---

## 🌐 Topologia de Infraestrutura (GCP)

A infraestrutura foi dividida em duas instâncias virtuais no **Google Cloud Compute Engine** dentro da mesma região, interagindo através de redes públicas e privadas[cite: 3]:

| Nó de Infraestrutura | Nome da Máquina | Função no Ecossistema |
| :--- | :--- | :--- |
| **VM 1 (Servidor)** | `autodiag-server` | Executa o Gateway FastAPI, os 3 nós gRPC e serve o frontend SPA[cite: 3]. |
| **VM 2 (Cliente)** | `autodiag-client` | Nó isolado executando scripts concorrentes para validação de carga e integridade[cite: 3]. |
| **Base de Dados Gerida** | `autodiag-db` (Cloud SQL) | PostgreSQL 17 hospedado em VPC segura acessado pelos serviços via credenciais encriptadas[cite: 3]. |

---

## 📂 Estrutura do Repositório

```text
autodiag/
├── billing_service/
│   └── server.py              # Microsserviço gRPC de Faturação e Mão de Obra
├── catalog_service/
│   └── server.py              # Microsserviço gRPC de Catálogo e Inventário
├── client/
│   └── cliente_concorrencia.py # Script cliente multithread para testes de carga
├── diagnostic_service/
│   └── server.py              # Microsserviço gRPC Orquestrador de Diagnósticos
├── frontend/
│   └── index.html             # Interface Web do utilizador (SPA)
├── gateway/
│   └── main.py                # API Gateway FastAPI (REST + Autenticação JWT)
├── protos/
│   ├── billing.proto          # Definição dos contratos do BillingService
│   ├── catalog.proto          # Definição dos contratos do CatalogService
│   ├── diagnostic.proto       # Definição dos contratos do DiagnosticService
│   └── *_pb2*.py              # Stubs compilados pelo compilador protoc
├── .env.example               # Modelo de variáveis de ambiente do cluster
├── iniciar_sistema.sh         # Script Shell para orquestração e boot dos serviços
├── requirements.txt           # Dependências Python do ecossistema
└── README.md                  # Documentação técnica do projeto
```
---

## 🔧 Configuração de Ambiente

Crie um ficheiro `.env` na raiz do projeto na VM Servidor com base nas variáveis necessárias:

```ini
# Configurações da Base de Dados Cloud SQL (PostgreSQL)
DB_HOST=10.x.x.x
DB_PORT=5432
DB_NAME=autodiag_db
DB_USER=autodiag_user
DB_PASS=sua_senha_segura

# Portas da Malha gRPC
CATALOG_PORT=50051
DIAGNOSTIC_PORT=50052
BILLING_PORT=50053

# Endereços para os Stubs gRPC
CATALOG_HOST=127.0.0.1
DIAGNOSTIC_HOST=127.0.0.1
BILLING_HOST=127.0.0.1

# Segurança e Autenticação
JWT_SECRET=chave_secreta_jwt_autodiag_2026
JWT_ALGORITHM=HS256
```

---

## 🚀 Guia de Instalação e Execução

### Na VM 1 (Servidor Principal)

1. Clone o repositório e aceda ao diretório:
   ```bash
   git clone [https://github.com/KaikeAndrade00/autodiag.git](https://github.com/KaikeAndrade00/autodiag.git)
   cd autodiag
   ```

2. Crie e ative o ambiente virtual Python:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. Instale os pacotes e dependências[cite: 4]:
   ```bash
   pip install -r requirements.txt
   ```

4. Inicie todos os nós da aplicação de forma automatizada[cite: 3, 4]:
   ```bash
   chmod +x iniciar_sistema.sh
   ./iniciar_sistema.sh
   ```

5. Aceda aos serviços disponíveis:
   * **Aplicação Web:** `http://<IP_PUBLICO_VM1>:8000/`
   * **Documentação Swagger (OpenAPI):** `http://<IP_PUBLICO_VM1>:8000/docs`

---

### Na VM 2 (Cliente de Testes Remoto)

1. Conecte-se à máquina cliente via SSH e prepare o ambiente[cite: 4]:
   ```bash
   sudo apt update && sudo apt install -y python3 python3-pip git
   git clone [https://github.com/KaikeAndrade00/autodiag.git](https://github.com/KaikeAndrade00/autodiag.git)
   cd autodiag/client
   pip install requests --break-system-packages
   ```

2. Execute o cliente interativo de concorrência[cite: 3, 4]:
   ```bash
   python3 cliente_concorrencia.py
   ```

   ---

## 🔌 Endpoints da API REST (Gateway)

Todas as rotas de negócio requerem cabeçalho de autorização HTTP com token Bearer (`Authorization: Bearer <TOKEN_JWT>`), com exceção da autenticação inicial[cite: 3]:

| Método | Rota | Protegida por JWT | Descrição da Operação |
| :---: | :--- | :---: | :--- |
| `POST` | `/auth/login` | Não | Autentica as credenciais do operador e retorna o Bearer Token JWT[cite: 3]. |
| `GET` | `/api/v1/veiculos` | Sim | Consulta o catálogo de veículos automotivos registados[cite: 3]. |
| `GET` | `/api/v1/pecas` | Sim | Consulta a listagem de peças com os saldos físicos em armazém[cite: 3]. |
| `POST` | `/api/v1/pecas/{id}/ajustar-estoque` | Sim | Adiciona ou deduz unidades de uma peça no inventário[cite: 3]. |
| `GET` | `/api/v1/diagnosticos` | Sim | Retorna o histórico de todas as Ordens de Serviço registadas[cite: 3]. |
| `POST` | `/api/v1/diagnosticos` | Sim | Abre uma nova OS associando cliente, veículo e sintoma reportado[cite: 3]. |
| `PUT` | `/api/v1/diagnosticos/{id}/concluir` | Sim | Dispara o fluxo transacional de liquidação, baixa e faturação da OS[cite: 3]. |

---

## 📜 Contratos de Interface (Protocol Buffers)

A comunicação entre o API Gateway e os serviços de retaguarda é regida por esquemas estritos definidos no diretório `protos/`[cite: 4]:

* **`catalog.proto`:** Mensagens `Peca`, `Veiculo`, `BaixarEstoqueRequest`, `AjustarEstoqueRequest` e o serviço `CatalogService` com os métodos `ListarPecas`, `ListarVeiculos`, `ObterPecaPorId`, `BaixarEstoque` e `AjustarEstoque`[cite: 4].
* **`diagnostic.proto`:** Mensagens `CriarDiagnosticoRequest`, `Diagnostico`, `ConcluirDiagnosticoRequest` e o serviço `DiagnosticService` com os métodos `CriarDiagnostico`, `ListarDiagnosticos` e `ConcluirDiagnostico`[cite: 4].
* **`billing.proto`:** Mensagens `GerarFaturaRequest`, `Fatura` e o serviço `BillingService` com o método `GerarFatura`[cite: 4].

---

## 🧪 Teste de Concorrência e Validação Distribuída

Para validar a integridade transacional perante a banca avaliadora, o script `client/cliente_concorrencia.py` recorre ao módulo `concurrent.futures.ThreadPoolExecutor` para submeter rajadas de requisições paralelas através da rede pública[cite: 3, 4].

### Cenário de Teste Executado
* **Nó Emissor:** VM `autodiag-client` (instância remota)[cite: 3].
* **Alvo:** Rota `PUT /api/v1/diagnosticos/{id}/concluir` na VM `autodiag-server`[cite: 3].
* **Carga Simultânea:** 5 requisições de quitação disparadas no mesmo milissegundo sobre a mesma OS em estado `EM ANÁLISE`[cite: 1, 3].

### Resultado Obtido no Terminal

```text
============================================================
                    RESULTADOS DO TESTE                     
============================================================
Thread   | Status HTTP    | Latência   | Resultado da Transação
------------------------------------------------------------
Req #1   | HTTP 200       | 275.4ms    | ✔ SUCESSO (Fatura gerada e estoque debitado)
Req #2   | HTTP 400       | 278.1ms    | 🛡️ BLOQUEADO (Esta ordem já foi concluída)
Req #3   | HTTP 400       | 281.0ms    | 🛡️ BLOQUEADO (Esta ordem já foi concluída)
Req #4   | HTTP 400       | 283.5ms    | 🛡️ BLOQUEADO (Esta ordem já foi concluída)
Req #5   | HTTP 400       | 285.2ms    | 🛡️ BLOQUEADO (Esta ordem já foi concluída)

[Conclusão Distribuída]
 • Transações Efetivadas: 1 (estoque debitado estritamente em 1 unidade)
 • Transações Bloqueadas: 4 (exclusão mútua garantida via FOR UPDATE)
============================================================
```

O ensaio comprova que, mesmo sob carga concorrente originada externamente, o mecanismo de bloqueio pessimista assegura consistência estrita e preserva o inventário contra débitos duplicados[cite: 3].
