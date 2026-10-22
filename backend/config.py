"""
Centralised file for configurations, connections and connectivity checks.
"""

import asyncpg
from contextlib import asynccontextmanager, AsyncExitStack
from fastapi import FastAPI
from openai import AsyncOpenAI

from azure.identity.aio import DefaultAzureCredential
from azure.keyvault.secrets.aio import SecretClient
from azure.cosmos.aio import CosmosClient
from azure.storage.blob.aio import BlobServiceClient

# Configuration Variables
AZURE_OPENAI_ENDPOINT = "https://fall26-ai.services.ai.azure.com"
AZURE_STORAGE_CONTAINER_NAME = "ticket2go-storage-container"
VAULT_URL = "https://ticket2go-keyvault.vault.azure.net/"
POSTGRES_DB = "ticket2go-psql-db"

# Mapping {variable name : secret in Key vault}
VAULT_SECRETS = {
    "api_key" : "azure-openai-api-key",
    "storage_con_str" : "azure-storage-connection-string",
    "cosmos_con_str" : "cosmos-connection-string",
    "psql_admin" : "psql-admin",
    "psql_password" : "psql-password",
    "psql_host" : "psql-host"
}

async def get_secret(client, secret_name):
    """
    Retrieves secret value from Azure Key Vault
    """
    secret = await client.get_secret(secret_name)
    if not secret.value:
        raise ValueError(
            f"Required Key Vault secret is empty: {secret_name}"
        )
    return secret.value

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan parameters for fastapi app.
    
    Shared resources: Azure identity, clients for key vault, cosmosdb, postgresql, 
    """
    async with AsyncExitStack() as stack:

        azure_credential = DefaultAzureCredential()
        stack.push_async_callback(azure_credential.close)

        key_vault_client = SecretClient(vault_url=VAULT_URL , credential=azure_credential)
        stack.push_async_callback(key_vault_client.close)

        # Retrieve all secrets
        secrets = {
            k : await get_secret(key_vault_client, v) 
            for k, v in VAULT_SECRETS.items()
            }

        cosmos_client = CosmosClient.from_connection_string(secrets["cosmos_con_str"])
        stack.push_async_callback(cosmos_client.close)

        psql_uri = f"postgresql://{secrets["psql_admin"]}:{secrets["psql_password"]}@{secrets["psql_host"]}/{POSTGRES_DB}?sslmode=require"
        
        postgres_client = await asyncpg.create_pool(
            dsn=psql_uri,
            min_size=2,
            max_size=10,
        )
        stack.push_async_callback(postgres_client.close)

        storage_client = BlobServiceClient.from_connection_string(secrets["storage_con_str"])
        stack.push_async_callback(storage_client.close)

        container_client = storage_client.get_container_client(AZURE_STORAGE_CONTAINER_NAME)
        stack.push_async_callback(container_client.close)

        ai_client = AsyncOpenAI(api_key=secrets["api_key"])
        stack.push_async_callback(ai_client.close)

        # ---- Expose Clients to application ----
        app.state.credential = azure_credential
        app.state.key_vault = key_vault_client
        app.state.cosmos = cosmos_client
        app.state.postgres = postgres_client
        app.state.container = container_client
        app.state.openai = ai_client

        yield
        # AsyncStackExit will close all connections automatically
    

async def check_postgres(app):
    """
    Checks connectivity to PostgreSQL Server.
    """
    try:
        async with app.state.postgres.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception:
        return False

async def check_storage_container(app):
    """
    Checks connectivity to Storage Container.
    """
    try:
        await app.state.container.get_container_properties()
        return True
    except Exception:
        return False

async def check_key_vault(app):
    """
    Checks connectivity to Key Vault.
    """
    try:
        await app.state.key_vault.get_secret("psql-host")
        return True
    except Exception:
        return False
    
async def check_cosmos(app):
    """
    Checks connectivity to CosmosDB server.
    """
    try:
        await app.state.cosmos.read()
        return True
    except Exception:
        return False
