# IAM policies

**Internship project:** This work was completed as part of the Loka Machine Learning Internship. References to "Week X", task numbers, and labs follow the original internship curriculum.

The IAM documents this lab needs. They hold `${PLACEHOLDERS}` rather than real account
IDs and resource names, so nothing account-specific is committed — the values come from
your `.env` at the moment you apply them.

| File | What it is | Applied to |
| --- | --- | --- |
| `agentcore-execution-role-trust-policy.json` | Lets AgentCore assume the execution role | the role's trust policy |
| `agentcore-execution-role-permissions-policy.json` | What the deployed MCP server may do | the role, as an inline policy |

### Who these apply to

Phases 1–3 create no IAM resources at all. When you run `ingest.py`, the local server, or
the container locally, boto3 uses **your** credentials, so every call reaches AWS as your
own identity (`.../AWSReservedSSO_.../you@example.com`) and your existing permissions
decide what works.

Phase 4 changes that. The deployed container runs on AWS infrastructure with your laptop
out of the picture, so it cannot borrow your credentials — it needs an identity of its own.
That is the execution role: AgentCore assumes it and injects its temporary credentials, so
calls arrive as `.../assumed-role/$ROLE_NAME/BedrockAgentCore-<session>`.


## Setup

Before running any commands, copy `.env.example` and fill in the values used by the policy
templates:

```bash
cp .env.example .env
```

Then load them in your shell:

```bash
set -a && . ./.env && set +a
```

Start a credentialed shell for the IAM commands below:

```bash
aws-vault exec "$AWS_PROFILE" -- bash
```

Run the setup and teardown commands inside that shell, then type `exit` when finished.

This reads `.env` and exports all variables, so the commands below can access `$ROLE_NAME`, `$AWS_REGION`, etc.
(`set -a` makes values visible to child processes; without it they stay local to your shell.)

Each command fills the placeholders in with `envsubst` and pipes the result straight into
the AWS CLI, so nothing is written to disk. If a placeholder is missing from `.env`, the
diff check in step 3 will catch it — the live policy will contain the literal `${...}` and
the diff will fail.

## 1. Create the execution role

```bash
aws iam create-role \
  --role-name "$ROLE_NAME" \
  --assume-role-policy-document "$(envsubst < iam/agentcore-execution-role-trust-policy.json)" \
  --description "Execution role for the lab1 article-search MCP on AgentCore Runtime" \
  --tags Key=Project,Value="$PROJECT_TAG"
```

The `Project=<your-project-name>` tag is **required**. An Organizations SCP denies untagged
creation, and the error names the missing permission rather than the missing tag. Use whatever
value your SCP requires (e.g., `ml-interns-mk-2026`).

## 2. Attach the permissions policy

```bash
aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name article-search-mcp-runtime \
  --policy-document "$(envsubst < iam/agentcore-execution-role-permissions-policy.json)"
```

## 3. Check what AWS actually has

```bash
aws iam get-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name article-search-mcp-runtime \
  --query 'PolicyDocument.Statement[].{Sid:Sid,Resource:Resource}' --output table
```

To prove the live policy still matches this file:

```bash
diff <(envsubst < iam/agentcore-execution-role-permissions-policy.json | python3 -m json.tool --sort-keys) \
     <(aws iam get-role-policy \
         --role-name "$ROLE_NAME" \
         --policy-name article-search-mcp-runtime \
         --query 'PolicyDocument' --output json | python3 -m json.tool --sort-keys) \
  && echo "live policy matches this file"
```

## 4. Re-apply after editing a file

Both commands replace in place, so they are safe to re-run.

```bash
# permissions
aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name article-search-mcp-runtime \
  --policy-document "$(envsubst < iam/agentcore-execution-role-permissions-policy.json)"

# trust policy -- note this is a different command from the one that creates it
aws iam update-assume-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-document "$(envsubst < iam/agentcore-execution-role-trust-policy.json)"
```

No redeploy needed. The role is assumed per request, so changes take effect within
seconds — useful, because permission gaps only surface at run time.

## 5. Tear down

```bash
aws iam delete-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name article-search-mcp-runtime

aws iam delete-role --role-name "$ROLE_NAME"
```

Delete the AgentCore runtime first — IAM will not remove a role that is still in use.

---

## Using a different account or names

Nothing to edit in the JSON — set these in `.env` and the commands pick them up:

| `.env` variable | Used for |
| --- | --- |
| `ACCOUNT` | your AWS account ID |
| `AWS_REGION` | region |
| `ROLE_NAME` | the execution role |
| `ECR_REPO` | ECR repository the role may pull |
| `VECTOR_BUCKET`, `VECTOR_INDEX` | the S3 Vectors index the role may query |
| `RUNTIME_NAME` | AgentCore runtime and log-group prefix |
| `PROJECT_TAG` | value required for the `Project` tag |

## Why the permissions look the way they do

- **Every ARN is scoped to the runtime's resources** — one ECR repository, one vector index,
  and one log-group prefix. `bedrock:InvokeModel` uses the region's foundation-model ARN
  pattern so the embedding model can remain configurable in `.env`.
- **No X-Ray or `cloudwatch:PutMetricData`,** although AWS's sample execution-role policy
  for AgentCore includes both. That sample assumes you instrumented the container with the
  ADOT SDK; this one is plain FastMCP, so nothing ever calls them. Checked rather than
  assumed: X-Ray held no traces for the window covering a verified invocation, and the
  runtime's metrics land in `AWS/Bedrock-AgentCore` — published by the service, not by us,
  and a reserved namespace a customer role cannot write to anyway. Add them back the day
  the container actually emits telemetry.
- **`s3vectors:GetVectors` is required alongside `QueryVectors`.** `query_vectors` runs
  with `returnMetadata=True`, which fetches the matched vectors' data. `QueryVectors` alone
  lets you find matches, but `GetVectors` is needed to read them. Without both, searches
  fail at runtime with `not authorized to perform: s3vectors:GetVectors` — invisible locally
  because your SSO credentials are broad, only discovered when deployed under the execution
  role's least-privilege permissions.
- **The trust policy carries the confused-deputy conditions** AWS recommends for service
  principals: `aws:SourceAccount` plus an `aws:SourceArn` pattern, so no other account can
  induce AgentCore into assuming this role.
- **`ecr:CreateRepository` needs the `Project` tag.** That is a condition on the *request*
  and cannot be expressed in a policy document — pass `--tags` on every create command.
