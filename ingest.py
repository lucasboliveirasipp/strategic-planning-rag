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
import sys
import time
from glob import glob
from uuid import uuid4
from typing import List, Optional

# --- identificação de caminhos e pastas do agente ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PASTA_DATA = os.path.join(BASE_DIR, "data")
PASTA_INGESTAO = os.path.join(BASE_DIR, "ingestao_documentos")

# --- carregamento de configurações da azure ---
try:
    if BASE_DIR not in sys.path:
        sys.path.append(BASE_DIR)
    import config
    from config import embeddings
except ImportError:
    print("❌ erro: falha ao importar config.py.")
    sys.exit(1)

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from langchain_community.vectorstores import AzureSearch
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from langchain_community.document_loaders import Docx2txtLoader

INDEX_NAME = os.environ.get("INDEX_NAME", "agente-planejamento-estrategico-v1")

def get_markdown_with_azure(file_path: str) -> str:
    try:
        client = DocumentIntelligenceClient(
            endpoint=os.environ["ENDPOINT_DOC"],
            credential=AzureKeyCredential(os.environ["KEY_DOC"])
        )
        with open(file_path, "rb") as f:
            poller = client.begin_analyze_document(
                "prebuilt-layout",
                body=f,
                output_content_format="markdown"
            )
            result = poller.result()
        return result.content
    except Exception as e:
        print(f"⚠️ Erro ao ler pdf ({os.path.basename(file_path)}): {e}")
        return ""

# --- extração de texto de arquivos word ---
def read_word_file(file_path: str) -> str:
    try:
        loader = Docx2txtLoader(file_path)
        docs = loader.load()
        return "\n".join([d.page_content for d in docs])
    except Exception as e:
        print(f"⚠️ Erro ao ler word ({os.path.basename(file_path)}): {e}")
        return ""

# --- fluxo principal de indexação de documentos ---
def ingest_files(target_files: Optional[List[str]] = None):
    
    arquivos = []

    # Se receber uma lista específica, processa só ela.
    # Se não receber nada (None), varre as pastas (comportamento antigo).
    if target_files:
        print(f"🚀 Iniciando ingestão INCREMENTAL de {len(target_files)} arquivo(s)...")
        arquivos = target_files
    else:
        print(f"🚀 Iniciando ingestão COMPLETA no índice: {INDEX_NAME}")
        for pasta in [PASTA_DATA, PASTA_INGESTAO]:
            if os.path.exists(pasta):
                arquivos.extend(glob(os.path.join(pasta, "*.pdf")))
                arquivos.extend(glob(os.path.join(pasta, "*.docx")))

    if not arquivos:
        print("⚠️ Nenhum arquivo encontrado para indexar.")
        return

    # conexão com banco de vetores (azure search)
    vector_store = AzureSearch(
        azure_search_endpoint=os.environ["ENDPOINT_SEARCH"],
        azure_search_key=os.environ["KEY_SEARCH"],
        index_name=INDEX_NAME,
        embedding_function=embeddings
    )

    # fatiamento do texto em blocos (chunks)
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=4000,
        chunk_overlap=400
    )

    # processamento e envio de dados para a nuvem
    for i, caminho in enumerate(arquivos):
        nome = os.path.basename(caminho)
        print(f"\n[{i+1}/{len(arquivos)}] 📄 Processando: {nome}")

        texto = ""
        if nome.lower().endswith(".pdf"):
            texto = get_markdown_with_azure(caminho)
        elif nome.lower().endswith(".docx"):
            texto = read_word_file(caminho)

        if not texto:
            print(f"   ⚠️ Pulando {nome} (texto vazio ou erro)")
            continue

        chunks = text_splitter.split_text(texto)
        docs_upload = [
            Document(
                page_content=c,
                metadata={"source": nome, "chunk_id": str(uuid4())}
            )
            for c in chunks
        ]

        if not docs_upload:
            continue

        # envio para azure em lotes de 20
        batch_size = 20
        print(f"   Enviando {len(docs_upload)} chunks para Azure...")
        for k in range(0, len(docs_upload), batch_size):
            batch = docs_upload[k: k + batch_size]
            try:
                vector_store.add_documents(batch)
                time.sleep(0.5) 
            except Exception as e:
                print(f"🔴 Erro ao enviar lote: {e}")

    print("\n🏁 Ingestão Finalizada!")

if __name__ == "__main__":
    ingest_files()