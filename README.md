# AWS Semantic-Search MCP Service

**Internship project:** This work was completed as part of the Loka Machine Learning Internship. References to "Week X", task numbers, and labs follow the original internship curriculum.

Developed by Atanas Vitanov during the Loka ML internship.

A [FastMCP](https://gofastmcp.com) server that performs semantic search over 10 ML papers using **Amazon Bedrock** embeddings and **S3 Vectors**, deployed to **Amazon Bedrock AgentCore Runtime**.

## Status

| Phase | Result |
| --- | --- |
| 1. Prepare data | Done — 438 vectors from 10 papers |
| 2. Build FastMCP server | Done — `list_articles`, `search_articles` |
| 3. Dockerize | Done — ARM64 image, 89 MB |
| 4. Deploy to AgentCore | Done — runtime READY, verified |

## How it works

```
10 papers (arXiv / JMLR) ──► S3 ──► src/ingest.py ──► S3 Vectors (438 × 1024, cosine)
                                    read → clean → chunk → embed        ▲
                                                                        │ query_vectors
                                                      src/mcp_server.py ────┘
                                                            │ MCP
                                          tests/test_retrieval.py  /  AgentCore Runtime
```

- **Corpus:** 10 classic ML papers (Attention, ResNet, BERT, GANs, Adam, XGBoost, Dropout,
  Batch Norm, word2vec, DQN). `data/articles.json` is the manifest.
- **Chunking:** 220 words, 40-word overlap, after stripping repeated headers/footers and
  rejoining words hyphenated across line breaks. Keys are `<pdf-stem>#<chunk_id>`, so
  re-ingesting overwrites in place instead of duplicating.
- **Embeddings:** Bedrock `amazon.titan-embed-text-v2:0`, 1024-dim, normalised.
- **Index:** one S3 Vectors index, cosine distance. Each vector carries `article_title`,
  `article_url`, `chunk_id` and `text` (`text` is non-filterable, so it does not count
  against the filterable-metadata budget).

---

# Reproducing on another machine

## Prerequisites

| Tool | Version | Install |
| --- | --- | --- |
| uv | 0.11.9+ | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Python | 3.11+ | uv installs if missing |
| AWS CLI | v2.36.8+ | for AWS API commands |
| Docker + buildx | 28.x+ | for ARM64 builds |

Enable ARM64 on Linux (Docker Desktop has it):
```bash
docker run --privileged --rm tonistiigi/binfmt --install arm64
docker buildx use desktop-linux
docker buildx inspect --bootstrap
```

## 1. AWS access and credentials

You need an AWS account with Bedrock and S3 Vectors enabled in your region (us-east-1 used here).

```bash
set -a && . ./.env && set +a
aws-vault exec "$AWS_PROFILE" -- aws sts get-caller-identity
```

`aws-vault exec` injects temporary credentials into the command or shell it starts. To run
several project commands with one session, use a credentialed shell:

```bash
set -a && . ./.env && set +a
aws-vault exec "$AWS_PROFILE" -- bash
```

For several commands, you can use that shell instead of adding `aws-vault exec` to each
command; type `exit` when finished. On Linux, if the default `keyctl` backend is unavailable, set
`AWS_VAULT_BACKEND=secret-service` or another backend supported by your installation.

Verify the embedding model is available:
```bash
aws-vault exec "$AWS_PROFILE" -- aws bedrock list-foundation-models --region us-east-1 \
  --by-output-modality EMBEDDING | grep titan-embed-text-v2
```

**All resource creation requires a `Project=set-your-value` tag** — Organizations SCP denies creation without it.

## 2. Clone and install dependencies

```bash
git clone <repo> && cd aws-semantic-search-mcp
uv sync --locked
```

## 3. Configure .env

```bash
cp .env.example .env
```

Edit `.env`:
```bash
AWS_REGION="<your-region>"
AWS_PROFILE="<your-profile>"
DOCS_BUCKET="<bucket-for-pdfs>"
DOCS_PREFIX="<your-name>/raw/"
VECTOR_BUCKET="<your-name>-lab1-vectors"  # must be unique
VECTOR_INDEX="articles"
EMBED_MODEL="<set-up-your-embedding-model>"
EMBED_DIM="<set-up-embedding-dimension>"
ACCOUNT="<account-id>"
ECR_REPO="<your-name>-mk-interns-lab1-mcp"
IMAGE_TAG="v1"
ROLE_NAME="<your-name>-mk-interns-lab1-agentcore-role"
RUNTIME_NAME="<your_name>_article_search_mcp"
PROJECT_TAG="<project-tag-value>"
```

Load environment:
```bash
set -a && . ./.env && set +a
```

## 4. Get papers into S3

Run the fetch script:

```bash
aws-vault exec "$AWS_PROFILE" -- uv run scripts/fetch_papers.py
```

It compares the objects under `s3://${DOCS_BUCKET}/${DOCS_PREFIX}` with the filenames in
`data/articles.json`. If all papers exist, it exits without downloading. Otherwise, it
downloads only the missing papers, uploads them to S3, and cleans up the local copies.

## 5. Run ingestion (~60 seconds, ~$0.003)

```bash
aws-vault exec "$AWS_PROFILE" -- uv run src/ingest.py
# Output: stored 438 vectors from 10 articles
```

## 6. Test server locally (terminal 1)

```bash
aws-vault exec "$AWS_PROFILE" -- uv run src/mcp_server.py
```

In another terminal (terminal 2):
```bash
set -a && . ./.env && set +a
uv run tests/test_retrieval.py
```

## 7. Build and test Docker image

```bash
docker buildx build --builder desktop-linux --platform linux/arm64 \
  -t article-search-mcp:arm64 --load .

aws-vault exec "$AWS_PROFILE" -- docker run --rm -p 8000:8000 \
  -e AWS_REGION -e VECTOR_BUCKET -e VECTOR_INDEX \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN \
  article-search-mcp:arm64

# In another terminal, run the same test as step 6:
uv run tests/test_retrieval.py
```

## 8. Deploy to AgentCore

### 8.1 Create ECR repository
```bash
aws-vault exec "$AWS_PROFILE" -- aws ecr create-repository \
  --repository-name "$ECR_REPO" \
  --region "$AWS_REGION" \
  --tags Key=Project,Value="$PROJECT_TAG" \
  --query 'repository.repositoryUri' --output text
```

### 8.2 Push image to ECR

`desktop-linux` is Docker Desktop's ARM64-capable endpoint; using its socket lets an
AMD64 laptop build the image AgentCore requires. `docker_config_dir` is a temporary Docker
credential store: it avoids a broken or unconfigured local credential helper, and the `trap`
deletes the short-lived ECR token when this shell exits.

```bash
docker_host="$(docker context inspect desktop-linux --format '{{.Endpoints.docker.Host}}')"
docker_config_dir="$(mktemp -d)"
trap 'rm -rf "$docker_config_dir"' EXIT

DOCKER_CONFIG="$docker_config_dir" DOCKER_HOST="$docker_host" \
  aws-vault exec "$AWS_PROFILE" -- bash -c '
    set -euo pipefail
    aws ecr get-login-password --region "$AWS_REGION" \
      | docker login --username AWS --password-stdin "$REGISTRY"
    docker buildx build --platform linux/arm64 \
      --provenance=false --sbom=false \
      -t "$IMAGE" --push .
  '
```

### 8.3 Create execution role
See [`iam/README.md`](iam/README.md) for the full setup. Quick version:

```bash
aws-vault exec "$AWS_PROFILE" -- aws iam create-role \
  --role-name "$ROLE_NAME" \
  --assume-role-policy-document "$(envsubst < iam/agentcore-execution-role-trust-policy.json)" \
  --description "Execution role for lab1 MCP" \
  --tags Key=Project,Value="$PROJECT_TAG"

aws-vault exec "$AWS_PROFILE" -- aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name article-search-mcp-runtime \
  --policy-document "$(envsubst < iam/agentcore-execution-role-permissions-policy.json)"
```

### 8.4 Create AgentCore runtime

```bash
aws-vault exec "$AWS_PROFILE" -- uv run scripts/create_runtime.py
```

Wait for status to become `READY`, then save the printed ARN to `.env` as `AGENT_RUNTIME_ARN`.

### 8.5 Test deployed MCP

Run the default test queries:
```bash
set -a && . ./.env && set +a
aws-vault exec "$AWS_PROFILE" -- uv run tests/test_agentcore.py
```

Or customize with flags:
```bash
aws-vault exec "$AWS_PROFILE" -- uv run tests/test_agentcore.py --query "how does dropout prevent overfitting?" --top-k 5
aws-vault exec "$AWS_PROFILE" -- uv run tests/test_agentcore.py --list-articles                    # show all articles first
aws-vault exec "$AWS_PROFILE" -- uv run tests/test_agentcore.py --query "what is gradient boosting?" --top-k 10
```

## 9. Tear down

```bash
aws-vault exec "$AWS_PROFILE" -- aws bedrock-agentcore-control delete-agent-runtime --region "$AWS_REGION" \
  --agent-runtime-id "${AGENT_RUNTIME_ARN##*/}"
aws-vault exec "$AWS_PROFILE" -- aws iam delete-role-policy --role-name "$ROLE_NAME" --policy-name article-search-mcp-runtime
aws-vault exec "$AWS_PROFILE" -- aws iam delete-role --role-name "$ROLE_NAME"
aws-vault exec "$AWS_PROFILE" -- aws ecr delete-repository --repository-name "$ECR_REPO" --region "$AWS_REGION" --force

# the data, if you want it gone too (the deployment does not manage this)
aws-vault exec "$AWS_PROFILE" -- aws s3vectors delete-index --vector-bucket-name "$VECTOR_BUCKET" --index-name "$VECTOR_INDEX" --region "$AWS_REGION"
aws-vault exec "$AWS_PROFILE" -- aws s3vectors delete-vector-bucket --vector-bucket-name "$VECTOR_BUCKET" --region "$AWS_REGION"
```

---

## What you get

| Resource | From |
| --- | --- |
| 10 ML papers | arXiv (9) + JMLR (1) |
| 438 vectors | Bedrock Titan Embed v2, 1024-dim, cosine distance |
| Deployed MCP | FastMCP on Streamable HTTP, ARM64 container |
| Code | `src/` (server + ingestion), `scripts/`, `tests/` |
| Config | `.env` (gitignored, not in image) |
| IAM policies | `iam/` (placeholder templates, no secrets committed) |

## Cost

- Ingestion: ~149k tokens (**$0.003**)
- Storage: 438 vectors + metadata (~2.4 MB, <$0.01/month)
- Queries: one embedding + one `query_vectors` per search

## Troubleshooting

| Issue | Fix |
| --- | --- |
| `KeyError: 'VECTOR_BUCKET'` | Run `set -a && . ./.env && set +a` |
| Model not found | `aws-vault exec "$AWS_PROFILE" -- aws bedrock list-foundation-models` — ensure it's enabled |
| `port 8000 already allocated` | Kill the bare server: `Ctrl-C` |
| Docker build: `no match for platform` | ARM64 emulation missing — enable it (see Prerequisites) |
| `explicit deny in a service control policy` | Missing `--tags Key=Project,Value="$PROJECT_TAG"` |
| Deployed searches fail but local works | Execution role missing `s3vectors:GetVectors` — check [`iam/README.md`](iam/README.md) |
| `CreateAgentRuntime` → `ValidationException` | Runtime name must be letters/digits/underscores only (no hyphens) |

## Code layout

```
src/
  common.py            config, cached AWS clients, embed()
  ingest.py            PDF → clean → chunk → embed → S3 Vectors
  mcp_server.py        FastMCP: list_articles, search_articles
data/
  articles.json        manifest (PDF → title, source URL, download URL)
scripts/
  fetch_papers.py      download the papers and upload them to S3
  create_runtime.py    create the AgentCore runtime
tests/
  test_retrieval.py    exercises local article retrieval
  test_agentcore.py    exercises the deployed runtime (SigV4-signed)
iam/                   IAM policy templates for the AgentCore execution role
Dockerfile             ARM64 build; ships src/ and data/ only
.env.example           config template
```

Only `src/` and `data/` reach the container — `scripts/` and `tests/` run from a laptop.
