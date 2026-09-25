# Standard library imports
import argparse
import asyncio
import getpass
import logging
import os
import subprocess

# Third-party / Google Cloud imports
import agentplatform
from agentplatform import types
from agentplatform.frameworks.adk import AdkApp
import google.auth
import google.auth.transport.requests
from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.auth.credential_manager import CredentialManager
from google.adk.integrations.agent_identity import GcpAuthProvider
from google.adk.integrations.agent_registry import AgentRegistry

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# =====================================================================
# Consolidated Environment Variables & Configuration
# =====================================================================
PROJECT_ID = os.environ.get("PROJECT_ID", "pratik-ag-vpcsc-test")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
BUCKET = os.environ.get("STAGING_BUCKET", "agent-temp-bucket-fsaft")
REG_LOCATION = os.environ.get("AGENT_REGISTRY_LOCATION", "global")
EGRESS_GATEWAY = os.environ.get("EGRESS_GATEWAY", "gateway")
MODEL_NAME = os.environ.get("MODEL_NAME", "gemini-2.5-flash")
AGENT_DISPLAY_NAME = os.environ.get("AGENT_DISPLAY_NAME", f"gcp_agent-{getpass.getuser()}")
ALLOWED_MCP_SERVERS = {
    s.strip()
    for s in os.environ.get(
        "ALLOWED_MCP_SERVERS",
        "storage.googleapis.com,agentregistry.googleapis.com",
    ).split(",")
    if s.strip()
}

if not PROJECT_ID:
    raise ValueError("Environment variable GOOGLE_CLOUD_PROJECT is required.")

os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID
os.environ["GOOGLE_GENAI_USE_ENTERPRISE"] = "True"

logging.info("Environment Configuration:")
logging.info(f"  PROJECT_ID (GOOGLE_CLOUD_PROJECT): {PROJECT_ID}")
logging.info(f"  LOCATION (GOOGLE_CLOUD_LOCATION): {LOCATION}")
logging.info(f"  BUCKET (STAGING_BUCKET): {BUCKET}")
logging.info(f"  REG_LOCATION (AGENT_REGISTRY_LOCATION): {REG_LOCATION}")
logging.info(f"  EGRESS_GATEWAY: {EGRESS_GATEWAY}")
logging.info(f"  MODEL_NAME: {MODEL_NAME}")
logging.info(f"  AGENT_DISPLAY_NAME: {AGENT_DISPLAY_NAME}")
logging.info(f"  ALLOWED_MCP_SERVERS: {sorted(ALLOWED_MCP_SERVERS)}")

# =====================================================================
# Agent Platform, Auth & MCP Toolset Initialization
# =====================================================================
if BUCKET:
    agentplatform.init(project=PROJECT_ID, location=LOCATION, staging_bucket=BUCKET)

CredentialManager.register_auth_provider(GcpAuthProvider())


def _header_provider(context: ReadonlyContext) -> dict[str, str]:
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(google.auth.transport.requests.Request())
    return {
        "Authorization": f"Bearer {credentials.token}",
        "Content-Type": "application/json",
    }


registry = AgentRegistry(project_id=PROJECT_ID, location=REG_LOCATION)

mcp_toolsets = []
page_token = None
while True:
    response = registry.list_mcp_servers(page_token=page_token)
    for server in response.get("mcpServers", []):
        server_name = server.get("name")
        display_name = server.get("displayName")
        if not server_name or display_name not in ALLOWED_MCP_SERVERS:
            continue
        try:
            toolset = registry.get_mcp_toolset(server_name)
            toolset._header_provider = _header_provider
            mcp_toolsets.append(toolset)
            logging.info(f"Added MCP toolset from registry: {display_name} ({server_name})")
        except Exception as e:
            logging.warning(f"Failed to load MCP toolset for {server_name}: {e}")
    page_token = response.get("nextPageToken")
    if not page_token:
        break

registry._session = None


# =====================================================================
# Agent & App Definition
# =====================================================================
# Subclassing LlmAgent forces the remote Agent Engine to import this module
# when unpickling the agent, ensuring that top-level code is executed.
class CmdLlmAgent(LlmAgent):
    pass


gcp_agent = CmdLlmAgent(
    name="gcp_agent",
    model=MODEL_NAME,
    instruction=(
        "You are a helpful general AI assistant and execution expert. Your goal is to assist users with their queries, provide helpful explanations, and execute tasks when requested.\n\n"
        "Rules:\n"
        "1. **Planning**: Before executing tasks, plan the steps needed and ensure the tasks are appropriate and safe.\n"
        "2. **Tool Usage**: Discover right MCP tools and call them.\n"
        "3. **Output Format**: Present task outputs, logs, or results clearly using Markdown code blocks. If a task fails, analyze the error and propose a fix or troubleshooting steps.\n"
        "4. **Tone**: Be helpful, concise, friendly, and professional."
    ),
    tools=mcp_toolsets,
)

app = AdkApp(app_name="gcp_agent", agent=gcp_agent)


# =====================================================================
# Deployment Helpers & CLI
# =====================================================================
def get_config(app: AdkApp) -> dict:
    if not BUCKET:
        raise ValueError("Environment variable STAGING_BUCKET is required for deployment.")

    config = {
        "display_name": AGENT_DISPLAY_NAME,
        "identity_type": types.IdentityType.AGENT_IDENTITY,
        "requirements": "requirements.txt",
        "extra_packages": ["deploy_gcp_agent.py"],
        "staging_bucket": BUCKET if BUCKET.startswith("gs://") else f"gs://{BUCKET}",
    }

    if EGRESS_GATEWAY:
        gateway_resource = (
            EGRESS_GATEWAY
            if EGRESS_GATEWAY.startswith("projects/")
            else f"projects/{PROJECT_ID}/locations/{LOCATION}/agentGateways/{EGRESS_GATEWAY}"
        )
        config["agent_gateway_config"] = {
            "agent_to_anywhere_config": {
                "agent_gateway": gateway_resource,
            }
        }

    return config


def get_client() -> agentplatform.Client:
    return agentplatform.Client(project=PROJECT_ID, location=LOCATION)


def assign_agent_roles(agent_identity: str):
    roles = [
        "roles/aiplatform.expressUser",
        "roles/aiplatform.agentDefaultAccess",
        "roles/aiplatform.user",
        "roles/agentregistry.viewer",
        "roles/storage.viewer",
        "roles/logging.logWriter",
        "roles/monitoring.metricWriter",
        "roles/iap.egressor",
        "roles/mcp.toolUser",
    ]
    for role in roles:
        command = (
            f"gcloud projects add-iam-policy-binding {PROJECT_ID} "
            f'--member="principal://{agent_identity}" --role="{role}"'
        )
        logging.info(f"Adding IAM role {role} for agent identity...")
        try:
            subprocess.run(command, shell=True, check=True)
        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to add IAM role binding {role}: {e}")
    os.environ["AGENT_IDENTITY"] = "principal://{agent_identity}"
    logging.info(f"Agent Identity: {agent_identity}")


async def create():
    client = get_client()
    logging.info("Deploying gcp_agent to Agent Engine...")
    remote_agent = client.runtimes.create(agent=app, config=get_config(app))

    agent_identity = remote_agent.api_resource.spec.effective_identity
    assign_agent_roles(agent_identity)

    logging.info(f"gcp_agent deployed successfully: {remote_agent.api_resource.name}")


async def update(agent_id: str):
    client = get_client()
    agent_name = (
        agent_id
        if agent_id.startswith("projects/")
        else f"projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{agent_id}"
    )

    logging.info("Updating gcp_agent in Agent Engine...")
    remote_agent = client.runtimes.update(
        name=agent_name,
        agent=app,
        config=get_config(app),
    )

    agent_identity = remote_agent.api_resource.spec.effective_identity
    assign_agent_roles(agent_identity)

    logging.info(f"gcp_agent updated successfully: {remote_agent.api_resource.name}")


async def main():
    parser = argparse.ArgumentParser(description="Create or update gcp_agent deployment.")
    subparsers = parser.add_subparsers(dest="action", required=True)

    subparsers.add_parser("create", help="Create a new gcp_agent deployment")

    update_parser = subparsers.add_parser("update", help="Update an existing gcp_agent deployment")
    update_parser.add_argument("--agent-id", required=True, help="Reasoning Engine ID or resource name")

    args = parser.parse_args()
    if args.action == "create":
        await create()
    elif args.action == "update":
        await update(args.agent_id)


if __name__ == "__main__":
    asyncio.run(main())
