"""Dados da campanha ficam em SQL; texto criativo só vira evento por ação do usuário."""
from __future__ import annotations

import sqlite3

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CampaignInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    summary: str = Field(default="", max_length=4000)
    house_rules: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Informe um nome para a campanha.")
        return value.strip()


class EventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Descreva o acontecimento.")
        return value.strip()


def get_campaign(conn: sqlite3.Connection, campaign_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["events"] = [dict(event) for event in conn.execute(
        "SELECT id, text, created_at FROM campaign_events WHERE campaign_id = ? ORDER BY id", (campaign_id,)
    )]
    return result


def campaign_context(conn: sqlite3.Connection, campaign_id: int) -> tuple[str, list[str]]:
    """Orçamento explícito: resumo/regras + últimos 12 eventos até 6 mil caracteres."""
    row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if row is None:
        raise ValueError("Campanha não encontrada.")
    total = conn.execute("SELECT COUNT(*) FROM campaign_events WHERE campaign_id = ?", (campaign_id,)).fetchone()[0]
    events = conn.execute(
        "SELECT id, text FROM campaign_events WHERE campaign_id = ? ORDER BY id DESC LIMIT 12", (campaign_id,)
    ).fetchall()
    selected, size = [], 0
    for event in events:
        if size + len(event["text"]) > 6000:
            break
        selected.append(f"Evento {event['id']}: {event['text']}")
        size += len(event["text"])
    warnings = []
    if len(selected) < total:
        warnings.append("O contexto inclui apenas os acontecimentos mais recentes que cabem no limite. Atualize o resumo para preservar fatos antigos importantes.")
    text = (
        f"Campanha: {row['name']}\nResumo confirmado pelo usuário:\n{row['summary']}\n"
        f"Regras da mesa (não são regras oficiais):\n{row['house_rules'] or 'Nenhuma registrada.'}\n"
        "Acontecimentos confirmados, em ordem:\n" + "\n".join(reversed(selected))
    )
    return text, warnings
