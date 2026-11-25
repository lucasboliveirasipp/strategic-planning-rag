# Carrega variáveis do arquivo .env mais próximo (pasta do projeto)
def _carregar_env():
    from pathlib import Path
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for pasta in Path(__file__).resolve().parents:
        if (pasta / ".env").exists():
            load_dotenv(pasta / ".env")
            break


_carregar_env()

import os
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings
 
# 1. CONFIGURAÇÃO DE REDE 
 
# 2. CREDENCIAIS: DOCUMENT INTELLIGENCE (PDFs)
 
# 3. CREDENCIAIS: AZURE AI SEARCH
os.environ["INDEX_NAME"] = "agente-planejamento-estrategico-v1"
 
# Variáveis auxiliares para compatibilidade com outros scripts
AZURE_SEARCH_ENDPOINT = os.environ["ENDPOINT_SEARCH"]
AZURE_SEARCH_API_KEY = os.environ["KEY_SEARCH"]
AZURE_SEARCH_INDEX_NAME = os.environ["INDEX_NAME"]
 
# 4. CREDENCIAIS: AZURE OPENAI
os.environ["GPT4"] = "gpt-4.1-mini"
 
# 5. CONFIGURAÇÃO DO LLM (CHAT)
llm = AzureChatOpenAI(
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    openai_api_version="2024-08-01-preview",
    deployment_name=os.environ["GPT4"],
    openai_api_type="azure",
    temperature=0
)
 
# 6. CONFIGURAÇÃO DE EMBEDDINGS
embeddings = AzureOpenAIEmbeddings(
    azure_deployment="text-embedding-ada-002",
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key=os.environ["OPENAI_API_KEY"],
    openai_api_version="2023-05-15",
    openai_api_type="azure"
)
 
print("✅ PARÂMETROS CARREGADOS (config.py)!")
