# GCP VPC-SC Agent (`gcp-vpcsc-agent`)

An AI assistant and execution agent built with the **Google Agent Development Kit (ADK)** and deployed to **Agent Engine** (Reasoning Engines) within a **VPC Service Controls (VPC-SC)** environment.

The agent dynamically discovers and invokes **Model Context Protocol (MCP)** tools registered in **Google Cloud Agent Registry** (such as Cloud Storage and Agent Registry MCP servers), authenticates using native **Agent Identity**, and routes outbound traffic through an **Agent Egress Gateway**.

---

## Key Features & Architecture

1. **Google ADK & Gemini Integration**
   - Built on `google.adk.agents.llm_agent.LlmAgent` (`CmdLlmAgent`) and wrapped in an `AdkApp`.
   - Powered by Gemini (`gemini-2.5-flash` by default) with Enterprise GenAI mode enabled (`GOOGLE_GENAI_USE_ENTERPRISE=True`).

2. **Dynamic MCP Tool Discovery via Agent Registry**
   - Queries **Google Cloud Agent Registry** (`google.adk.integrations.agent_registry.AgentRegistry`) at startup to list registered MCP servers.
   - Filters servers against an allowlist (`ALLOWED_MCP_SERVERS`, defaulting to `storage.googleapis.com` and `agentregistry.googleapis.com`) and attaches their MCP toolsets to the agent.
   - Injects fresh Google Cloud OAuth2 bearer tokens (`https://www.googleapis.com/auth/cloud-platform` scope) into MCP requests via a custom header provider.

3. **Native Agent Identity (`AGENT_IDENTITY`)**
   - Deploys with `IdentityType.AGENT_IDENTITY` and registers `GcpAuthProvider` with ADK's `CredentialManager`.
   - Automatically grants the deployed agent's effective identity (`principal://<agent_identity>`) the required project-level IAM roles after creation or update.

4. **VPC-SC Egress Gateway Support**
   - Configures `agent_gateway_config` (`agent_to_anywhere_config`) to route outbound traffic through a specified Google Cloud **Agent Gateway**, enabling compliant communication in VPC-SC protected projects.

5. **Remote Initialization Pattern**
   - Subclasses `LlmAgent` as `CmdLlmAgent` and packages `deploy_gcp_agent.py` via `extra_packages`. When Vertex AI Agent Engine unpickles the agent remotely, it imports `deploy_gcp_agent.py` and executes top-level initialization (auth provider registration and MCP toolset discovery).

```
┌──────────────────────────────────────────────────────────────────────┐
│                   Vertex AI Agent Engine (VPC-SC)                    │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ AdkApp ("gcp_agent")                                           │  │
│  │  ├── Model: gemini-2.5-flash                                   │  │
│  │  ├── Identity: AGENT_IDENTITY (GcpAuthProvider)                │  │
│  │  └── Tools: MCP Toolsets loaded from GCP Agent Registry        │  │
│  └───────────────┬────────────────────────────────┬───────────────┘  │
│                  │                                │                  │
│                  ▼                                ▼                  │
│      ┌───────────────────────┐        ┌───────────────────────┐      │
│      │  GCP Agent Registry   │        │ Agent Egress Gateway  │      │
│      │ (MCP Tool Discovery)  │        │ (agent_to_anywhere)   │      │
│      └───────────────────────┘        └───────────┬───────────┘      │
└───────────────────────────────────────────────────┼──────────────────┘
                                                    │
                                                    ▼
                                      ┌───────────────────────────┐
                                      │ Allowed MCP Servers       │
                                      │ - storage.googleapis.com  │
                                      │ - agentregistry.googleapis│
                                      └───────────────────────────┘
```

---

## Project Structure

- `deploy_gcp_agent.py` — Main module defining the ADK agent, MCP registry loading, IAM role binding helper, and CLI for creating or updating the Vertex AI Agent Engine deployment.
- `requirements.txt` — Python package dependencies required both locally and in the remote Agent Engine runtime.

---

## Prerequisites

1. **Python 3.10+**
2. **Google Cloud SDK (`gcloud`)** installed and initialized:
   ```bash
   gcloud auth login
   gcloud auth application-default login
   ```
3. **GCP Resources & Permissions**:
   - A GCP Project with **Vertex AI API**, **Agent Registry API**, and **Cloud Storage** enabled.
   - An existing **GCS Staging Bucket** in your project for deployment artifacts.
   - *(Optional)* An **Agent Gateway** resource configured in your project/region if using VPC-SC egress.
   - Your local principal must have permissions to deploy Agent Engine runtimes, read from Agent Registry, and modify project IAM policies (`roles/resourcemanager.projectIamAdmin` or equivalent) so `deploy_gcp_agent.py` can bind IAM roles to the agent identity.

---

## Installation

1. Clone the repository and navigate to the project directory:
   ```bash
   cd gcp-vpcsc-agent
   ```

2. Create and activate a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
   *(Note: Ensure the `agentplatform` SDK is installed in your environment if provided via an internal package index or wheel.)*

---

## Configuration

The agent and deployment script are configured via environment variables. If not set, the script falls back to the defaults defined in `deploy_gcp_agent.py`:

| Environment Variable | Default Value | Description |
| :--- | :--- | :--- |
| `PROJ_ID` | `pratik-ag-vpcsc-test` | Target Google Cloud Project ID (also sets `GOOGLE_CLOUD_PROJECT`). |
| `REGION` | `us-central1` | GCP region for Vertex AI Agent Engine and the Agent Gateway. |
| `STAGING_BUCKET` | `agent-temp-bucket-fsaft` | GCS bucket name or `gs://` URI used to stage deployment artifacts. |
| `AGENT_REGISTRY_LOCATION` | `global` | Location of the GCP Agent Registry used to discover MCP servers. |
| `AGW_NAME` | `gateway` | Name or full resource path (`projects/.../locations/.../agentGateways/...`) of the egress Agent Gateway. Set to `""` to omit gateway config. |
| `MODEL_NAME` | `gemini-2.5-flash` | Gemini model used by `CmdLlmAgent`. |
| `AGENT_DISPLAY_NAME` | `gcp_agent-<username>` | Display name for the deployed agent runtime in Vertex AI. |
| `ALLOWED_MCP_SERVERS` | `storage.googleapis.com,agentregistry.googleapis.com` | Comma-separated list of MCP server `displayName`s to load from Agent Registry. |

### Example Environment Setup

```bash
export PROJ_ID="my-vpcsc-project"
export REGION="us-central1"
export STAGING_BUCKET="my-agent-staging-bucket"
export AGENT_REGISTRY_LOCATION="global"
export AGW_NAME="my-egress-gateway"
export MODEL_NAME="gemini-2.5-flash"
export AGENT_DISPLAY_NAME="gcp_agent-prod"
export ALLOWED_MCP_SERVERS="storage.googleapis.com,agentregistry.googleapis.com"
```

---

## Usage

### 1. Deploy a New Agent (`create`)

To package the agent, deploy it as a new runtime in Vertex AI Agent Engine, and automatically bind IAM roles to its new Agent Identity:

```bash
python deploy_gcp_agent.py create
```

When deployment completes, the script logs the full resource name of the deployed agent:
```text
INFO - Agent Identity: agents.global.org-...
INFO - gcp_agent deployed successfully: projects/<PROJECT_ID>/locations/<REGION>/reasoningEngines/<AGENT_ID>
```

### 2. Update an Existing Agent (`update`)

To update an existing deployed agent in-place (using either the numeric Reasoning Engine ID or the full `projects/.../locations/.../reasoningEngines/...` resource name):

```bash
python deploy_gcp_agent.py update --agent-id <AGENT_ID>
```

Example with full resource name:
```bash
python deploy_gcp_agent.py update \
  --agent-id projects/pratik-ag-vpcsc-test/locations/us-central1/reasoningEngines/1234567890
```

---

## IAM Roles Automatically Assigned to the Agent

During both `create` and `update`, `assign_agent_roles()` extracts `remote_agent.api_resource.spec.effective_identity` and runs `gcloud projects add-iam-policy-binding` to grant the agent principal (`principal://<agent_identity>`) the following roles on `PROJ_ID`:

| IAM Role | Purpose |
| :--- | :--- |
| `roles/aiplatform.expressUser` | Access Vertex AI Express / GenAI features. |
| `roles/aiplatform.agentDefaultAccess` | Default runtime permissions required by Vertex AI Agent Engine. |
| `roles/aiplatform.user` | Invoke Vertex AI models and endpoints. |
| `roles/agentregistry.viewer` | Discover and read MCP servers and toolsets in GCP Agent Registry. |
| `roles/storage.viewer` | Read objects and metadata from Google Cloud Storage buckets. |
| `roles/logging.logWriter` | Write agent execution logs to Cloud Logging. |
| `roles/monitoring.metricWriter` | Emit telemetry and metrics to Cloud Monitoring. |
| `roles/iap.egressor` | Route outbound requests through the Agent Egress Gateway / IAP. |
| `roles/mcp.toolUser` | Execute tools hosted on Google Cloud MCP servers. |

---

## Querying the Deployed Agent

Once deployed, you can interact with the remote agent using the `agentplatform` SDK in Python:

```python
import agentplatform

PROJECT_ID = "pratik-ag-vpcsc-test"
LOCATION = "us-central1"
AGENT_ID = "<YOUR_REASONING_ENGINE_ID>"

client = agentplatform.Client(project=PROJECT_ID, location=LOCATION)
remote_agent = client.runtimes.get(
    name=f"projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{AGENT_ID}"
)

# Stream a query to the agent
for event in remote_agent.stream_query(
    user_id="test-user",
    message="List the GCS buckets in my project and summarize what MCP servers are registered.",
):
    print(event)
```

---
