from __future__ import annotations

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

import sys
import os
import logging
import time

# --- 1. configuração de caminho (path) ---
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

# --- 2. demais importações ---
import re
import pandas as pd
from uuid import uuid4
from datetime import datetime
from typing import Dict, List

# bibliotecas externas
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
import streamlit as st
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from azure.search.documents.indexes import SearchIndexClient

# Importações do projeto
try:
    from graph_builder import graph
    from ingest import ingest_files 
except ImportError as e:
    st.error(f"Erro ao importar módulos locais: {e}")
    class MockGraph:
        def get_state(self, *args, **kwargs): return {}
        def stream(self, *args, **kwargs): return []
    graph = MockGraph()
    def ingest_files(): pass

# configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- SISTEMA DE FEEDBACK E LOGS ---
FEEDBACK_AVAILABLE = False
try:
    from feedback_local import update_feedback
    from feedback_local import log_interaction
    FEEDBACK_AVAILABLE = True
    logger.info("✅ sistema de feedback carregado")
except ImportError as e:
    logger.warning(f"⚠️ sistema de feedback não disponível: {e}")
    def update_feedback(*args, **kwargs): pass
    def log_interaction(*args, **kwargs): pass

AGENT_NAME = "agente_planejamento_estrategico"

# --- importação do estilo visual ---
try:
    from streamlit_style import (
        init_page_config,
        apply_app_style,
        build_institutional_header,
        build_footer
    )
except ImportError as e:
    st.error(f"ERRO CRÍTICO: não foi possível importar 'streamlit_style'. detalhe: {e}")
    st.stop()

# --- 4. funções de dados ---
@st.cache_data(ttl=60)
def get_local_file_catalog():
    """Lista arquivos dinamicamente baseando-se no diretório atual do projeto."""
    try:
        target_folders = ["data", "ingestao_documentos"]
        docs = []

        for folder in target_folders:
            # Constrói o caminho relativo ao ambiente de execução
            folder_path = os.path.join(current_dir, folder)
            
            if os.path.exists(folder_path):
                files = [f for f in os.listdir(folder_path) if f.endswith(('.pdf', '.docx'))]
                for f in files:
                    file_path = os.path.join(folder_path, f)
                    stats = os.stat(file_path)
                    
                    upload_time = datetime.fromtimestamp(stats.st_mtime).strftime('%d/%m/%Y %H:%M')
                    size_mb = stats.st_size / (1024 * 1024)
                    
                    docs.append({
                        "Nome do Documento": f,
                        "Data de Upload": upload_time,
                        "Tamanho (MB)": size_mb,
                        "Origem": "Sistema" if folder == "data" else "Upload Usuário"
                    })
            
        if docs:
            df = pd.DataFrame(docs)
            df = df.drop_duplicates(subset=["Nome do Documento"])
            df = df.sort_values(by="Data de Upload", ascending=False)
            return df
        return pd.DataFrame()

    except Exception as e:
        logger.error(f"Erro ao listar arquivos: {e}")
        return pd.DataFrame()
    
def get_index_usage():
    try:
        endpoint = os.environ.get("ENDPOINT_SEARCH")
        key = os.environ.get("KEY_SEARCH")
        index_name = os.environ.get("INDEX_NAME")
        client = SearchIndexClient(endpoint=endpoint, credential=AzureKeyCredential(key))
        stats = client.get_index_statistics(index_name)
        return stats['storage_size'] / (1024**3) 
    except Exception as e:
        logger.error(f"Erro ao consultar storage: {e}")
        return 0.0
    
# --- FUNÇÃO DELETAR ---
def delete_document_process(filename: str):
    """
    Baixa os metadados de todos os chunks e filtra no Python para garantir
    que arquivos com underscores (_) ou acentos sejam encontrados e deletados.
    """
    msgs = []
    import json
    
    # 1. Remoção Local (Mantida)
    target_folder = os.path.join(current_dir, "ingestao_documentos")
    filepath = os.path.join(target_folder, filename)
    
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            msgs.append(f"✅ Arquivo local removido.")
        else:
            msgs.append(f"⚠️ Arquivo local não estava na pasta (ok, seguimos para Azure).")
    except Exception as e:
        msgs.append(f"❌ Erro local: {e}")

    # 2. Remoção na Azure AI Search 
    try:
        endpoint = os.environ.get("ENDPOINT_SEARCH")
        key = os.environ.get("KEY_SEARCH")
        index_name = os.environ.get("INDEX_NAME")
        
        search_client = SearchClient(endpoint=endpoint, index_name=index_name, credential=AzureKeyCredential(key))
        
        msgs.append(f"🔍 Varrendo índice por: '{filename}'...")
        
        results = search_client.search(
            search_text="*", 
            select=["id", "metadata"],
            include_total_count=True
        )
        
        ids_to_delete = []
        
        for doc in results:
            try:
                meta_str = doc.get("metadata")
                if meta_str:
                    meta_json = json.loads(meta_str)
                    source_no_azure = meta_json.get("source")
                    
                    if source_no_azure == filename:
                        ids_to_delete.append({"id": doc["id"]})
            except:
                continue

        # Executa a exclusão
        if ids_to_delete:
            # Azure aceita deletar em lotes (até 1000 por vez)
            batch_size = 1000
            for i in range(0, len(ids_to_delete), batch_size):
                batch = ids_to_delete[i:i + batch_size]
                search_client.delete_documents(documents=batch)
            
            msgs.append(f"✅ SUCESSO: {len(ids_to_delete)} fragmentos removidos da nuvem.")
        else:
            msgs.append("ℹ️ Arquivo não encontrado no índice (verifique se o nome está idêntico).")
            
    except Exception as e:
        msgs.append(f"❌ Erro Azure: {e}")
        
    return msgs

# --- 5. css local (estilo dos cards) ---
def _inject_component_styles():
    st.markdown("""
    <style>
        .status-banner {
            background-color: #FFFFFF;
            color: #2D3748;
            padding: 16px 24px;
            border-radius: 12px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.05);
            margin-bottom: 24px;
            border: 1px solid #EDF2F7;
            display: flex; align-items: center; justify-content: space-between;
            font-family: 'Segoe UI', sans-serif !important;
        }
        .status-text {
            color: #1E3A8A;
            font-weight: 700; letter-spacing: 0.5px;
            text-transform: uppercase; font-size: 0.9rem;
        }
        .status-pill {
            background-color: #1E3A8A;
            color: white !important;
            padding: 6px 16px; border-radius: 6px;
            font-weight: 600; font-size: 0.85rem;
        }
        .manual-card h3 {
            color: #1E3A8A !important;
            font-family: 'Segoe UI', sans-serif !important;
            font-weight: 700 !important;
            font-size: 1.25rem !important;
            margin-bottom: 12px !important;
        }
        .manual-card p, .manual-card li, .manual-card span {
            color: #2D3748 !important;
            font-family: 'Segoe UI', sans-serif !important;
            line-height: 1.6;
        }
        .file-item {
            display: flex; align-items: center; gap: 10px; 
            padding: 8px 0; border-bottom: 1px solid #EDF2F7;
            color: #2D3748;
            font-family: 'Segoe UI', sans-serif !important;
        }
    </style>
    """, unsafe_allow_html=True)


# --- 6. componentes de interface (fontes) ---
def _render_sources(sources: List[Dict]) -> None:
    if not sources: return
    unique_files = set()
    clean_list = []
    for source in sources:
        name = source.get("filepathField") or source.get("source") or "Documento sem nome"
        if name not in unique_files:
            unique_files.add(name)
            clean_list.append(name)

    with st.expander("📚 Fontes Consultadas", expanded=False):
        for name in clean_list:
            st.markdown(f"""
            <div class="file-item">
                <span style="font-size: 1.2em;">📄</span>
                <span style="font-weight: 500;">{name}</span>
            </div>""", unsafe_allow_html=True)

# --- 7. página do guia rápido (manual) ---
def render_manual_page():
    tab1, tab2 = st.tabs(["📍 Comece por Aqui", "📂 Base de Conhecimento"])
    
    with tab1:
        st.markdown("""
        <div class="status-banner">
            <div class="status-text">ESPECIALISTA ESTRATÉGICO</div>
            <div class="status-pill">Status: Online 🟢</div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("""
        <div class="manual-card">
            <h3>✅ Capacidades</h3>
            <ul style="padding-left: 20px;">
                <li style="margin-bottom:10px;"><b>Apoio à Decisão:</b> atuo como seu parceiro estratégico para correlacionar informações de mercado com as Diretrizes e Ambições do Grupo Acme Seguros.</li>
                <li style="margin-bottom:10px;"><b>Análise Documental:</b> minha inteligência é focada exclusivamente no cruzamento dos documentos ingeridos na ferramenta (PDFs de Auto/RE, Vida e Previdência, CAP, etc).</li>
                <li><b>Ambiente de Experimentação (PoC):</b> importante ressaltar que as notícias e dados de mercado utilizados são baseados estritamente nos arquivos carregados manualmente; não possuo atualização em tempo real via internet.</li>
            </ul>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("""
        <div class="manual-card">
            <h3>🔍 O que perguntar?</h3>
            <ul style="padding-left: 20px;">
                <li style="margin-bottom:10px;"><b>Defesa de Mercado:</b> cole ou anexe uma notícia sobre a concorrência e peça uma análise comparativa com nossos planos internos para identificar se estamos protegidos ou expostos.</li>
                <li style="margin-bottom:10px;"><b>Novas Frentes de Receita:</b> questione sobre as iniciativas de expansão fora do ecossistema tradicional, como modelos de assinatura, parcerias digitais e vendas para não-correntistas.</li>
                <li><b>Gaps de Performance:</b> peça um diagnóstico sobre nossa agilidade tecnológica e metas de eficiência comparadas aos movimentos mais recentes do mercado.</li>
            </ul>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("""
        <div class="manual-card" style="margin-bottom: 10px; padding-bottom: 10px;">
            <h3>💡 Exemplos de Perguntas</h3>
            <p style="font-style: italic; font-size: 0.9em; margin-bottom: 10px;">Clique no ícone de copiar no canto direito de cada pergunta:</p>
        </div>
        """, unsafe_allow_html=True)

        exemplos = [
            "Notei que a Beta Seguros lançou recentemente um modelo de assinatura de serviços voltado para clientes sem seguro. Temos alguma iniciativa semelhante prevista em nossas diretrizes?",
            "Quais são as ambições estratégicas até 2027?",
            "Existe alguma diretriz no Ciclo Estratégico sobre uso de IA Generativa?",
            "A Gama Seguros já está colhendo resultados de eficiência com IA no atendimento. Olhando para o nosso planejamento estratégico, nós temos algum projeto prático de IA para a operação rodando agora?"
        ]

        for pergunta in exemplos:
            st.code(pergunta, language="text")
        
        st.markdown("<br>", unsafe_allow_html=True)

        st.markdown("""
        <div class="manual-card">
            <h3>🚫 Limitações</h3>
            <ul style="padding-left: 20px;">
                <li style="margin-bottom:8px;">Minhas respostas baseiam-se estritamente nos documentos ingeridos na ferramenta.</li>
                <li style="margin-bottom:8px;"><b>Não invento dados:</b> se a diretriz não existir no documento, informarei.</li>
                <li><b>Confidencialidade:</b> as análises geradas são para uso interno estratégico.</li>
                <li><b>Capacidade da PoC:</b> limite de 10 arquivos por lote e máximo de 50MB no total para garantir a agilidade da indexação.</li>
            </ul>
        </div>
        """, unsafe_allow_html=True)

    # --- TAB 2: GESTÃO DE ARQUIVOS ---
    with tab2:
        st.markdown("### 🎲 Capacidade de Armazenamento")
        st.markdown("Exibe o volume atual de armazenamento consumido pelos documentos (Sistema + Uploads) dentro do limite de 15GB.")
        
        # 1. Barra de Progresso
        consumo_gb = get_index_usage()
        limite_gb = 15.0
        percentual_real = (consumo_gb / limite_gb) * 100
        percentual_visual = max(percentual_real, 1.0) if consumo_gb > 0 else 0

        col_metric, col_bar = st.columns([1, 3])
        
        with col_metric:
             st.metric(label="Uso Atual", value=f"{percentual_real:.4f}%", delta=f"{consumo_gb:.4f} GB")

        with col_bar:
            st.markdown(f"""
                <div style="margin-top: 20px;">
                    <div style="
                        background-color: #E6EAF1; 
                        border-radius: 15px; 
                        height: 25px; 
                        width: 100%; 
                        overflow: hidden;
                    ">
                        <div style="
                            background-color: #2563EB; 
                            width: {percentual_visual}%; 
                            height: 100%;
                            transition: width 0.5s;
                        "></div>
                    </div>
                    <div style="text-align: right; font-size: 0.8em; color: #718096; margin-top: 5px;">
                        Utilizado: {consumo_gb:.4f} GB / Limite: {limite_gb} GB
                    </div>
                </div>
            """, unsafe_allow_html=True)
        
        st.markdown("---")
        
        # 2. Listagem de Arquivos
        st.markdown("### 📄 Catálogo de Documentos")
        st.markdown("Estes arquivos foram adicionados ao projeto e são usados pelo agente como referência.")
        
        with st.spinner("Atualizando catálogo..."):
            df = get_local_file_catalog()
            
        if df is not None and not df.empty:
            
            # Separa os dados
            df_sistema = df[df["Origem"] == "Sistema"].copy()
            df_usuario = df[df["Origem"] == "Upload Usuário"].copy()
            
            # Tabela Sistema (Fixo)
            if not df_sistema.empty:
                st.markdown("**🔒 Arquivos do Sistema (Protegidos)**", help=
                            "Estes arquivos representam a base de conhecimento oficial do projeto. Eles são gerenciados pela equipe técnica e são fixos para garantir que o agente siga as regras da empresa.")
                st.dataframe(
                    df_sistema.drop(columns=["Origem"]),
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Nome do Documento": st.column_config.TextColumn("Nome", width="large"),
                        "Tamanho (MB)": st.column_config.NumberColumn("Tamanho", format="%.2f MB"),
                        "Data de Upload": st.column_config.TextColumn("Data", width="medium"),
                    }
                )
                st.markdown("<br>", unsafe_allow_html=True)
            
            # Tabela Usuário (Leitura)
            if not df_usuario.empty:
                st.markdown("**👤 Uploads (Gerenciável)**", help=
                            "Estes são arquivos que foram inseridos pela interface em 'Ingestão de Documentos' para fazer testes ou análises pontuais (ex: uma notícia de mercado). Eles consomem seu limite de armazenamento e você tem total liberdade para excluí-los.")
                st.dataframe(
                    df_usuario.drop(columns=["Origem"]),
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Nome do Documento": st.column_config.TextColumn("Nome", width="large"),
                        "Tamanho (MB)": st.column_config.NumberColumn("Tamanho", format="%.2f MB"),
                        "Data de Upload": st.column_config.TextColumn("Data", width="medium"),
                    }
                )
                
                # --- 3. BLOCO DE EXCLUSÃO ---
                st.markdown("---")
                st.markdown("### 🗑️ Área de Exclusão")
                st.markdown("Utilize para remover arquivos antigos ou de teste e liberar espaço para novos documentos. Apenas 'Uploads (Gerenciável)' podem ser excluídos.")
                
                with st.container(border=True):
                    st.warning("⚠️ Atenção: a exclusão remove o arquivo da base de conhecimento permanentemente.")
                    
                    # Lista de opções para o dropdown
                    lista_arquivos = df_usuario["Nome do Documento"].tolist()
                    
                    # Componente Multiselect
                    arquivos_selecionados = st.multiselect(
                        "Selecione os arquivos que deseja remover:",
                        options=lista_arquivos,
                        placeholder="Clique ou digite para buscar..."
                    )
                    
                    if arquivos_selecionados:
                        st.markdown(f"**Selecionados para exclusão:** {len(arquivos_selecionados)} arquivo(s).")
                        
                        # Botão de confirmação
                        if st.button("Confirmar Exclusão", type="primary"):
                            progress_bar = st.progress(0, text="Iniciando...")
                            
                            for i, filename in enumerate(arquivos_selecionados):
                                logs = delete_document_process(filename)
                                
                                # Feedback visual (Toast)
                                for log in logs:
                                    st.toast(log)
                                
                                # Atualiza barra
                                progress_bar.progress((i + 1) / len(arquivos_selecionados), text=f"Removendo {filename}...")
                            
                            time.sleep(1)
                            st.cache_data.clear() 
                            st.success("Arquivos removidos com sucesso!")
                            st.rerun()

            elif df_usuario.empty:
                st.info("Você ainda não fez upload de nenhum arquivo.")
                
        else:
            st.warning("⚠️ Nenhum documento encontrado.")

    # --- 8. estado do grafo ---
def _get_graph_state(thread_id: str) -> List[BaseMessage]:
    state = graph.get_state({"configurable": {"thread_id": thread_id}})
    return state.values.get("messages", []) if state and state.values else []

# --- 9. app principal (interface) ---
def build_page(is_authenticated: bool = True, *args, **kwargs) -> None:
    apply_app_style()
    _inject_component_styles()

    if not is_authenticated:
        st.error("🔒 Acesso negado. Por favor, realize o login.")
        return

    if "thread_id" not in st.session_state: st.session_state["thread_id"] = str(uuid4())
    if "current_page" not in st.session_state: st.session_state["current_page"] = "chat"
    if "edit_mode" not in st.session_state: st.session_state["edit_mode"] = False
    
    if "uploader_key" not in st.session_state: st.session_state["uploader_key"] = 0
    if "latest_response_id" not in st.session_state: st.session_state["latest_response_id"] = None
    
    with st.sidebar:
        
        st.markdown("### Navegação")
        
        if st.button("💬 Agente Estratégico", type="primary" if st.session_state["current_page"] == "chat" else "secondary"):
            st.session_state["current_page"] = "chat"
            st.rerun()
        
        if st.button("📘 Guia Rápido", type="primary" if st.session_state["current_page"] == "manual" else "secondary"):
            st.session_state["current_page"] = "manual"
            st.rerun()
        
        st.markdown("---")
                
        st.markdown("#### 📂 Ingestão de Documentos", help=(
            "**Guia de Ingestão:**\n\n"
            "1. **Evite Fluxogramas:** assim como a maioria das IAs, a interpretação de diagramas visuais é limitada. Priorize textos.\n"                
            "2. **Formatos:** apenas arquivos .pdf e .docx.\n"
            "3. **Validação:** após processar, faça uma pergunta para validar o conteúdo.\n"
            "4. **Feedback:** use o 'joinha' (👍/👎) que está abaixo de cada resposta do agente para avaliar a qualidade."
        ))
        
        # Definimos os limites da PoC
        LIMITE_ARQUIVOS = 10 
        LIMITE_TAMANHO_MB = 50 

        uploaded_files = st.file_uploader(
            f"Upload PDF/DOCX (Máx {LIMITE_ARQUIVOS} arquivos)", 
            type=["pdf", "docx"], 
            accept_multiple_files=True,
            key=f"uploader_{st.session_state['uploader_key']}"
        )
        
        if uploaded_files:
            # 1. Validação de Quantidade
            if len(uploaded_files) > LIMITE_ARQUIVOS:
                st.error(f"⚠️ Limite excedido: Máx {LIMITE_ARQUIVOS} arquivos.")
            else:
                # 2. Validação de Tamanho Total
                tamanho_total = sum(f.size for f in uploaded_files) / (1024 * 1024) # Converte para MB
                
                if tamanho_total > LIMITE_TAMANHO_MB:
                    st.error(f"⚠️ Volume total ({tamanho_total:.1f}MB) excede o limite de {LIMITE_TAMANHO_MB}MB.")
                else:
                    if st.button("Processar"):
                        with st.spinner("Indexando APENAS novos arquivos..."):
                            upload_path = os.path.join(current_dir, "ingestao_documentos")
                            if not os.path.exists(upload_path): 
                                os.makedirs(upload_path)
                            
                            new_files_paths = []
                            for f in uploaded_files:
                                save_path = os.path.join(upload_path, f.name)
                                with open(save_path, "wb") as w:
                                    w.write(f.getbuffer())
                                new_files_paths.append(save_path)
                            
                            # Chama a ingestão incremental (usando a função que ajustamos no ingest.py)
                            try:
                                ingest_files(target_files=new_files_paths)
                            except TypeError:
                                # Fallback caso o ingest.py não tenha sido atualizado ainda
                                st.warning("Aviso: executando ingestão completa (ingest.py antigo).")
                                ingest_files()

                            st.cache_data.clear() 
                            st.session_state["uploader_key"] += 1
                            st.success("Processamento concluído!")
                            time.sleep(1)
                            st.rerun()                    
                            
        
        # --- SEÇÃO DE BARRA DE PROGRESSO ---
        st.markdown("---")
        consumo_gb = get_index_usage()
        limite_gb = 15.0
        percentual_real = (consumo_gb / limite_gb) * 100
        percentual_visual = max(percentual_real, 1.0) if consumo_gb > 0 else 0

        st.markdown(
            f"🎲 **Capacidade Utilizada {percentual_real:.4f}%**",
            help=f"Representa o volume ocupado na Base de Conhecimento pelos documentos que o agente leu. Se a barra encher, será necessário remover documentos antigos para inserir novos. Limite: {limite_gb}GB."
        )

        st.markdown(f"""
            <div style="
                background-color: #E6EAF1; 
                border-radius: 15px; 
                height: 18px; 
                width: 100%; 
                overflow: hidden;
                margin-bottom: 5px;
            ">
                <div style="
                    background-color: #2563EB; 
                    width: {percentual_visual}%; 
                    height: 100%;
                "></div>
            </div>
        """, unsafe_allow_html=True)
        
        st.caption(f"Utilizado: {consumo_gb:.4f} GB de {limite_gb} GB")
        
        if st.session_state["current_page"] == "chat":
            st.markdown("---")
            st.markdown("🖲️ **Controles**")
            
            if st.button("Limpar Conversa", icon="🗑️", type="primary"):
                st.session_state["thread_id"] = str(uuid4())
                st.session_state["edit_mode"] = False
                st.session_state["latest_response_id"] = None 
                st.rerun()
            
            history = _get_graph_state(st.session_state["thread_id"])
            if history and isinstance(history[-1], AIMessage):
                last_human = next((m for m in reversed(history) if isinstance(m, HumanMessage)), None)
                if last_human:
                    if st.button("Refazer Última Pergunta", type="secondary"): 
                        st.session_state["edit_mode"] = True
                        st.session_state["text_to_edit"] = last_human.content
                        st.rerun()
        
    # --- LÓGICA DE EXIBIÇÃO DA PÁGINA PRINCIPAL ---
    if st.session_state["current_page"] == "manual":
        build_institutional_header(
            title="📘 Guia Rápido", 
            subtitle="Tudo que você precisa saber: boas práticas, orientações e manual de referência",
            show_logo=False 
        )
        render_manual_page()
        build_footer()
    else:
        build_institutional_header(
            title="Agente Estratégico", 
            subtitle="Seu aliado estratégico na análise de mercado e diretrizes corporativas"
        )
        
        thread_id = st.session_state["thread_id"]
        config = {"configurable": {"thread_id": thread_id}}
        history = _get_graph_state(thread_id)
        final_prompt = None
        
        if not history:
            st.markdown("""
            <div class="manual-card" style="margin-bottom: 20px;">
                <h3>📚 Sobre este Agente</h3>
                <p style="margin-bottom: 12px;">
                    Sou seu assistente especializado em <b>Planejamento Estratégico</b>. Minha missão é ajudar você a analisar como estamos posicionados frente à concorrência e navegar pelo nosso <b>Ciclo Estratégico</b>:
                </p>
                <ul style="padding-left: 20px; margin-bottom: 12px;">
                    <li><b>Ambições e Diretrizes:</b> consulte os objetivos e planos para cada frente de negócio (Auto/RE, Vida e Previdência, CAP, etc).</li>
                    <li><b>Análise Competitiva:</b> compare movimentos do mercado e notícias de concorrentes com a nossa estratégia interna.</li>
                </ul>
                <p style="margin-bottom: 8px;"><b>💡 Exemplos do que você pode perguntar:</b></p>
                <ul style="padding-left: 20px; margin-bottom: 15px; font-style: italic; font-size: 0.95em;">
                    <li>"Quais são as ambições estratégicas até 2027?"</li>
                    <li>"Existe alguma diretriz sobre uso de IA Generativa?"</li>
                    <li>"Notei que a Beta Seguros lançou um modelo de assinatura. Temos algo semelhante?"</li>
                    <li>"Observe esta notícia [colar notícia] e compare se temos ambições ou diretrizes nesse mesmo assunto frente à concorrência."</li>
                </ul>
                <p style="font-size: 0.9em; color: #718096; border-top: 1px solid #EDF2F7; pt-10px;">
                    ✨ <i><b>Dica:</b> você pode colar uma notícia diretamente aqui ou anexar novos arquivos de <b>notícias ou documentos</b> pelo menu lateral em <b>"Ingestão de Documentos"</b>.</i>
                </p>
            </div>
            """, unsafe_allow_html=True)

        if st.session_state["edit_mode"]:
            st.info("📝 **Editando pergunta:**")
            with st.form("edit_form"):
                txt = st.text_area("Sua pergunta:", value=st.session_state.get("text_to_edit", ""))
                c1, c2 = st.columns([1, 4])
                if c1.form_submit_button("🚀 Reenviar", type="primary"):
                    final_prompt = txt
                    st.session_state["edit_mode"] = False
                if c2.form_submit_button("❌ Cancelar"):
                    st.session_state["edit_mode"] = False
                    st.rerun()
        else:
            if user_input := st.chat_input("Digite sua pergunta ou cole uma notícia..."):
                final_prompt = user_input

        chat_container = st.container()
        with chat_container:
            for i, msg in enumerate(history):
                role = "user" if isinstance(msg, HumanMessage) else "assistant"
                with st.chat_message(role):
                    st.markdown(msg.content)
                    if role == "assistant": 
                        _render_sources(msg.additional_kwargs.get("sources", []))
                        if i == len(history) - 1:
                            if st.session_state.get("latest_response_id"):
                                update_feedback(
                                    agent_name=AGENT_NAME, 
                                    thread_id=thread_id, 
                                    response_id=st.session_state["latest_response_id"]
                                )

            if final_prompt and not st.session_state.get("edit_mode"):
                with st.chat_message("user"): 
                    st.markdown(final_prompt)
                
                with st.chat_message("assistant"):
                    with st.spinner("Analisando diretrizes..."):
                        inputs = {"messages": [HumanMessage(content=final_prompt)], "query": final_prompt}
                        events = graph.stream(inputs, config, stream_mode="values")
                        
                        final_state = {}
                        for event in events: final_state = event
                        
                        if msgs := final_state.get("messages"):
                            resp = next((m for m in reversed(msgs) if isinstance(m, AIMessage) and m.name == "writer"), None)
                            if resp:
                                st.markdown(resp.content)
                                _render_sources(resp.additional_kwargs.get("sources", []))
                                
                                if FEEDBACK_AVAILABLE:
                                    response_id = str(uuid4())
                                    st.session_state["latest_response_id"] = response_id
                                    try:
                                        log_interaction(
                                            agent_name=AGENT_NAME,
                                            thread_id=thread_id,
                                            response_id=response_id,
                                            user_question=final_prompt,
                                            chatbot_response=resp.content
                                        )
                                        st.rerun() 
                                    except Exception as e:
                                        logger.error(f"Erro ao logar: {e}")
                
                if final_prompt and "edit_form" in str(st.session_state): st.rerun()
                
if __name__ == "__main__":
    build_page()