from typing import TypedDict, Annotated, Sequence, List, Any
from langchain_core.messages import BaseMessage
import operator

class AgentState(TypedDict):
    """
    Define o estado do grafo. 
    Aqui ficam guardadas as informações que trafegam entre os nós (Nodes).
    """
    
    # Histórico de mensagens (Chat History)
    # O 'operator.add' garante que novas mensagens sejam adicionadas à lista (append)
    # em vez de sobrescrever a lista antiga.
    messages: Annotated[Sequence[BaseMessage], operator.add]
    
    # A pergunta atual do usuário (Query)
    query: str
    
    # O contexto recuperado do Azure Search (Lista de Documentos)
    context: List[Any]