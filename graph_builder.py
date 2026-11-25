from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from state import AgentState
from nodes import retrieve_node, generate_node

# inicializa o grafo de estado
workflow = StateGraph(AgentState)

# define os passos (nós) que o robô vai executar
workflow.add_node("retrieve", retrieve_node)
workflow.add_node("writer", generate_node)

# define a ordem de execução:
# 1. começa buscando documentos (retrieve)
# 2. depois escreve a resposta (writer)
# 3. fim
workflow.set_entry_point("retrieve")
workflow.add_edge("retrieve", "writer")
workflow.add_edge("writer", END)

# define a memória para guardar o histórico da conversa
memory = MemorySaver()

# compila o grafo para ser usado no aplicativo
graph = workflow.compile(checkpointer=memory)