# RAG Chatbot on Azure

A chatbot that answers questions about uploaded PDF files (Retrieval-Augmented Generation), deployed on Azure with Terraform, Docker and Azure Key Vault.

## Architecture

| Component | Technology |
|---|---|
| Frontend | Streamlit (`frontend.py`), port 8501 |
| Backend API | FastAPI (`backend.py`), port 5000 |
| LLM and embeddings | OpenRouter (OpenAI-compatible API) via LangChain |
| Vector store | ChromaDB (container) |
| Chat history | Azure Database for PostgreSQL (`advanced_chats` table) |
| Secrets | Azure Key Vault, read by the VM's managed identity |
| Hosting | Azure Linux VM (Ubuntu 24.04) running Docker Compose |
| Infrastructure as code | Terraform (`main.tf`) |
| CI/CD | GitHub Actions: build images, push to Docker Hub, deploy to the VM (`.github/workflows/deploy.yml`) |

## Backend endpoints

- `GET /load_chat/` - list saved chats
- `POST /save_chat/` - save a chat
- `POST /delete_chat/` - delete a chat
- `POST /upload_pdf/` - upload a PDF and index it in ChromaDB
- `POST /rag_chat/` - ask a question about the uploaded PDF
- `POST /chat/` - plain chat without a PDF

## Infrastructure (Terraform)

`main.tf` creates the resource group, virtual network, subnet, network security group (ports 22 and 8501), static public IP, the VM with a system-assigned managed identity, and a Key Vault with RBAC:

- the deploying user gets **Key Vault Secrets Officer** (to add secrets)
- the VM gets **Key Vault Secrets User** (to read them)

```bash
# ssh-keys/terraform-azure.pub must contain your SSH public key
# terraform.tfvars:
#   resource_group_name = "..."
#   subscription_id     = "..."
terraform init
terraform apply -target=azurerm_linux_virtual_machine.chatbot   # first run on an existing VM only
terraform apply
```

## Key Vault secrets

At startup, if `KEY_VAULT_NAME` is set, the backend loads these secrets into its environment:

`PROJ-DB-NAME`, `PROJ-DB-USER`, `PROJ-DB-PASSWORD`, `PROJ-DB-HOST`, `PROJ-DB-PORT`, `PROJ-OPENAI-API-KEY`, `PROJ-AZURE-STORAGE-SAS-URL`, `PROJ-AZURE-STORAGE-CONTAINER`, `PROJ-CHROMADB-HOST`, `PROJ-CHROMADB-PORT`

The VM's `.env` only needs:

```
KEY_VAULT_NAME=<key vault name>
DOCKERHUB_NAMESPACE=<docker hub username>
```

## Running on the VM

```bash
docker compose up -d --build
```

Then open `http://<VM public IP>:8501`.

## CI/CD

The workflow is set to run manually (Actions tab, then **Run workflow**). It needs these repository secrets: `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`, `AZURE_CREDENTIALS`, `RESOURCE_GROUP_NAME`, `VM_NAME`. The deploy job runs `update_app.sh` on the VM through `az vm run-command`.
