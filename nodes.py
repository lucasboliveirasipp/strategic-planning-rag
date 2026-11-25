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
 
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)
 
from langchain_community.vectorstores import AzureSearch
from langchain_core.messages import AIMessage
from langchain_core.prompts import PromptTemplate
from state import AgentState
from prompt import agent_prompt
 
# --- 2. IMPORTAR DO CONFIG (ATUALIZADO) ---
try:
    import config
    from config import embeddings, llm
    ENDPOINT = config.ENDPOINT_SEARCH
    KEY = config.KEY_SEARCH
    INDEX_NAME = config.INDEX_NAME
except ImportError as e:
    print(f"🔴 Erro crítico: Não foi possível importar 'config.py'. Detalhe: {e}")
    sys.exit(1)
except AttributeError:
    print("⚠️ Aviso: Variáveis não encontradas diretamente no config.py, tentando os.environ...")
    ENDPOINT = os.environ.get("ENDPOINT_SEARCH")
    KEY = os.environ.get("KEY_SEARCH")
    INDEX_NAME = os.environ.get("INDEX_NAME", "agente-planejamento-estrategico-v1")
 
# --- 3. CONEXÃO COM O ÍNDICE ---
vectorstore = None
 
if not ENDPOINT or not KEY:
    print("🔴 ALERTA: Endpoint ou Key da Azure Search não encontrados no config.py!")
else:
    try:
        print(f"🔌 Conectando ao índice: {INDEX_NAME}...")
        vectorstore = AzureSearch(
            azure_search_endpoint=ENDPOINT,
            azure_search_key=KEY,
            index_name=INDEX_NAME,
            embedding_function=embeddings
        )
        print("✅ Conexão com Azure Search estabelecida no nodes.py")
    except Exception as e:
        print(f"🔴 Erro ao conectar no Azure Search: {e}")
        vectorstore = None
 
# --- FUNÇÃO DO CLASSIFICADOR ---
def should_search_documents(query: str) -> bool:
    """
    Decide se a pergunta exige consulta aos documentos de Estratégia.
    """
    router_template = """
    Você é um classificador de intenções para o Chatbot de Planejamento Estratégico do Grupo Acme Seguros.
    Analise a entrada do usuário e responda APENAS com uma das palavras: "BUSCAR" ou "PULAR".
 
    CRITÉRIOS:
    - Responda **BUSCAR** se for: 
        - Perguntas sobre Estratégia, Metas, Ambições, Diretrizes.
        - Dúvidas sobre unidades de negócio: Auto/RE, Vida e Previdência, CAP, SAÚDE, Dental, Auto.
        - Análise de Notícias de Mercado, Concorrência, Inovação ou Tendências.
    - Responda **PULAR** se for: 
        - Saudações (oi, bom dia, tudo bem).
        - Perguntas de identidade (quem é você).
        - Conversa fiada ou nonsense.
 
    ENTRADA DO USUÁRIO: "{input}"
    SUA CLASSIFICAÇÃO:
    """
    try:
        prompt = PromptTemplate.from_template(router_template)
        chain = prompt | llm
        result = chain.invoke({"input": query})
        decision = result.content.strip().upper()
        print(f"🤖 Router de Estratégia: '{query}' -> {decision}")
        return "BUSCAR" in decision
    except Exception as e:
        print(f"⚠️ Erro no Router (Fallback = Buscar): {e}")
        return True
 
# --- NÓ DE BUSCA (RETRIEVE) ---
def retrieve_node(state: AgentState):
    query = state["query"]
    if not vectorstore:
        print("⚠️ AVISO: Vectorstore não inicializado. Pulando busca.")
        return {"context": []}
 
    # 2. Router decide
    precisa_buscar = should_search_documents(query)
    if not precisa_buscar:
        return {"context": []}
 
    # 3. Busca Vetorial
    try:
        print(f"🔍 Buscando documentos estratégicos para: {query}")
        documents = vectorstore.similarity_search(query, k=5)
        print(f"📄 Encontrados {len(documents)} documentos relevantes.")
        return {"context": documents}
    except Exception as e:
        print(f"🔴 Erro durante a busca vetorial: {e}")
        return {"context": []}
 
# --- NÓ DE GERAÇÃO ---
def generate_node(state: AgentState):
    query = state["query"]
    context = state["context"] 
    messages = state["messages"] 
    # Formata o histórico do chat
    history_text = "\n".join(
        [f"{msg.type.upper()}: {msg.content}" for msg in messages[:-1]] 
    )
    # Prepara o texto dos documentos
    if context:
        context_text = "\n\n".join([doc.page_content for doc in context])
    else:
        context_text = "Nenhum documento relevante encontrado na base de estratégia."
    try:
        chain = agent_prompt | llm
        response = chain.invoke({
            "context": context_text, 
            "question": query,
            "history": history_text 
        })
        content_resp = response.content
    except Exception as e:
        content_resp = "Desculpe, ocorreu um erro ao gerar a resposta. Por favor, tente novamente."
        print(f"🔴 Erro na geração LLM: {e}")
    # Extrai metadados para mostrar as fontes na interface
    sources_data = []
    if context:
        for doc in context:
            # Tenta pegar o nome do arquivo, se não tiver, usa "Documento Indexado"
            filename = (doc.metadata.get("source") or 
                        doc.metadata.get("filepathField") or 
                        doc.metadata.get("title") or 
                        "Documento Indexado")
            sources_data.append({
                "filepathField": filename,
                "content": doc.page_content
            })
 
    final_message = AIMessage(
        content=content_resp,
        name="writer",
        additional_kwargs={"sources": sources_data} 
    )
    return {"messages": [final_message]}