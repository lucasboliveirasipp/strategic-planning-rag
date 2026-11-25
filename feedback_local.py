"""Registro local de interações e feedback (👍/👎) do agente.

Substitui os módulos de feedback do hub onde o agente rodava originalmente.
Se MONGO_CONNECTION_STRING estiver definido, grava no MongoDB; senão, guarda
em memória (o app funciona normalmente, só não persiste entre reinícios).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_FUSO_BRASILIA = timezone(timedelta(hours=-3))


def get_local_datetime_for_mongo() -> datetime:
    """Data/hora atual no fuso de Brasília (sem tzinfo, como o Mongo espera)."""
    return datetime.now(_FUSO_BRASILIA).replace(tzinfo=None)


class _ColecaoEmMemoria:
    """Subconjunto mínimo da API de uma coleção do pymongo."""

    def __init__(self) -> None:
        self.docs: List[Dict[str, Any]] = []

    def insert_one(self, doc: Dict[str, Any]):
        self.docs.append(dict(doc))
        return type("InsertResult", (), {"inserted_id": len(self.docs)})()

    def _match(self, doc: Dict[str, Any], filtro: Dict[str, Any]) -> bool:
        return all(doc.get(k) == v for k, v in (filtro or {}).items())

    def find_one(self, filtro: Optional[Dict[str, Any]] = None, *_a, **_k):
        return next((d for d in self.docs if self._match(d, filtro or {})), None)

    def find(self, filtro: Optional[Dict[str, Any]] = None, *_a, **_k):
        return [d for d in self.docs if self._match(d, filtro or {})]

    def update_one(self, filtro: Dict[str, Any], update: Dict[str, Any], upsert: bool = False, **_k):
        doc = self.find_one(filtro)
        if doc is None and upsert:
            doc = dict(filtro)
            self.docs.append(doc)
        if doc is not None:
            doc.update(update.get("$set", {}))
            for campo, valor in update.get("$push", {}).items():
                doc.setdefault(campo, []).append(valor)
        return type("UpdateResult", (), {"matched_count": int(doc is not None)})()


class _BancoEmMemoria(dict):
    def __getitem__(self, nome: str) -> _ColecaoEmMemoria:
        if nome not in self:
            super().__setitem__(nome, _ColecaoEmMemoria())
        return super().__getitem__(nome)


def _conectar():
    uri = os.getenv("MONGO_CONNECTION_STRING", "").strip()
    if uri:
        try:
            from pymongo import MongoClient

            cliente = MongoClient(uri, serverSelectionTimeoutMS=5000)
            cliente.admin.command("ping")
            return cliente[os.getenv("MONGO_DB_NAME", "feedbacks")]
        except Exception as exc:  # noqa: BLE001
            logger.warning("MongoDB indisponível (%s); usando armazenamento em memória.", exc)
    return _BancoEmMemoria()


MONGO = _conectar()


def log_interaction(agent_name: str, thread_id: str, response_id: str, **dados: Any) -> None:
    """Registra uma pergunta/resposta do agente."""
    MONGO[agent_name].update_one(
        {"thread_id": thread_id, "response_id": response_id},
        {"$set": {**dados, "agent_name": agent_name, "datetime": get_local_datetime_for_mongo()}},
        upsert=True,
    )


def update_feedback(agent_name: str, thread_id: str, response_id: str, **dados: Any) -> None:
    """Registra o feedback do usuário (ex.: feedback='positivo', comentario='...')."""
    MONGO[agent_name].update_one(
        {"thread_id": thread_id, "response_id": response_id},
        {"$set": {**dados, "feedback_datetime": get_local_datetime_for_mongo()}},
        upsert=True,
    )
