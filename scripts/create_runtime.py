"""Create AgentCore runtime for the MCP server."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from common import agentcore_control  # noqa: E402

account = os.environ.get("ACCOUNT")
region = os.environ.get("AWS_REGION")
runtime_name = os.environ.get("RUNTIME_NAME")
image_uri = os.environ.get("IMAGE")
role_name = os.environ.get("ROLE_NAME")
vector_bucket = os.environ.get("VECTOR_BUCKET")
vector_index = os.environ.get("VECTOR_INDEX")
project_tag = os.environ.get("PROJECT_TAG")

required = [
    "ACCOUNT",
    "AWS_REGION",
    "RUNTIME_NAME",
    "IMAGE",
    "ROLE_NAME",
    "VECTOR_BUCKET",
    "VECTOR_INDEX",
    "PROJECT_TAG",
]
missing = [v for v in required if not os.environ.get(v)]
if missing:
    raise ValueError(f"Missing environment variables: {', '.join(missing)}")

agentcore_client = agentcore_control()

print(f"Creating AgentCore runtime: {runtime_name}")
print(f"  Image: {image_uri}")
print(f"  Role: arn:aws:iam::{account}:role/{role_name}")

response = agentcore_client.create_agent_runtime(
    agentRuntimeName=runtime_name,
    description="Semantic search over 10 ML papers via S3 Vectors (lab1)",
    agentRuntimeArtifact={"containerConfiguration": {"containerUri": image_uri}},
    roleArn=f"arn:aws:iam::{account}:role/{role_name}",
    networkConfiguration={"networkMode": "PUBLIC"},
    protocolConfiguration={"serverProtocol": "MCP"},
    environmentVariables={
        "VECTOR_BUCKET": vector_bucket,
        "VECTOR_INDEX": vector_index,
    },
    tags={"Project": project_tag},
)

runtime_arn = response["agentRuntimeArn"]
status = response["status"]

print(f"\nRuntime created:")
print(f"  ARN: {runtime_arn}")
print(f"  Status: {status}")
print(f"\nSave this to .env as AGENT_RUNTIME_ARN:\n  AGENT_RUNTIME_ARN=\"{runtime_arn}\"")
