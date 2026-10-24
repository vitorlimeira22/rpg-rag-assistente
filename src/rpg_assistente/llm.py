"""Comunicação com o provedor; a busca e a montagem de fontes ficam em answer.py."""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
import math
import re

import groq

from .citations import normalize_citations
from .config import Settings


@dataclass(frozen=True)
class Generation:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    finish_reason: str | None


@dataclass(frozen=True)
class ToolCall:
    """Pedido do modelo, ainda não autorizado nem executado.

    ``arguments`` permanece uma string JSON: o orquestrador valida esquema,
    nome da ferramenta e limites antes de executar qualquer operação.
    """

    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class GenerationTurn(Generation):
    tool_calls: list[ToolCall] = field(default_factory=list)
    rate_remaining_tokens: int | None = None
    rate_reset_seconds: float | None = None
    rate_limit_tokens: int | None = None


class GenerationError(Exception):
    """Erro sem corpo da resposta, chave ou conteúdo do livro."""

    def __init__(self, message: str, code: int | None = None, provider_code: str | None = None):
        super().__init__(message)
        self.code = code
        self.provider_code = provider_code


def _nonnegative_header_int(value: str | None) -> int | None:
    if value is None or not re.fullmatch(r"[0-9]+", value):
        return None
    try:
        return int(value)
    except ValueError:
        # Cabeçalhos também são entrada externa: números enormes podem exceder
        # o limite de conversão do Python.
        return None


def _reset_seconds(value: str | None) -> float | None:
    if value is None or len(value) > 64:
        return None
    match = re.fullmatch(r"(?:(?P<minutes>[0-9]+)m)?(?P<seconds>[0-9]+(?:\.[0-9]+)?)s", value)
    if match is None:
        return None
    seconds = float(match["minutes"] or 0) * 60 + float(match["seconds"])
    return seconds if math.isfinite(seconds) and 0 <= seconds <= 86400 else None


def _tool_calls(message) -> list[ToolCall]:
    """Normaliza o protocolo sem interpretar JSON ou executar conteúdo."""

    calls = []
    seen_ids = set()
    for call in getattr(message, "tool_calls", None) or []:
        function = getattr(call, "function", None)
        call_id = getattr(call, "id", None)
        name = getattr(function, "name", None)
        arguments = getattr(function, "arguments", None)
        if (getattr(call, "type", None) != "function"
                or not isinstance(call_id, str) or not call_id.strip()
                or call_id in seen_ids
                or not isinstance(name, str) or not name.strip()
                or not isinstance(arguments, str)):
            raise GenerationError("O Groq retornou uma chamada de ferramenta em formato inválido.")
        seen_ids.add(call_id)
        calls.append(ToolCall(id=call_id, name=name, arguments=arguments))
    return calls


def generate_turn(settings: Settings, messages: list[dict], *,
                  tools: list[dict] | None = None,
                  tool_choice: str = "auto",
                  client: groq.Groq | None = None,
                  max_output_tokens: int | None = None,
                  response_format: dict | None = None) -> GenerationTurn:
    """Uma única chamada ao provedor, sem laço de agente nem retries.

    Aceita o histórico de mensagens, incluindo respostas com papel ``tool``.
    Cotas, número de rodadas e autorização das ferramentas pertencem à camada
    de aplicação. O cliente injetado continua sob responsabilidade do chamador.
    """

    token_limit = settings.max_output_tokens if max_output_tokens is None else max_output_tokens
    if isinstance(token_limit, bool) or not isinstance(token_limit, int) or token_limit < 1:
        raise ValueError("O limite de saída deve ser um inteiro positivo.")
    if tool_choice not in ("auto", "required"):
        raise ValueError("tool_choice deve ser auto ou required.")
    if tool_choice == "required" and not tools:
        raise ValueError("tool_choice required exige ferramentas disponíveis.")
    try:
        # Uma tentativa por turno: retries implícitos consumiriam cota sem
        # aparecer individualmente no contador diário da aplicação.
        context = (nullcontext(client) if client is not None else
                   groq.Groq(api_key=settings.groq_api_key,
                             timeout=settings.timeout_seconds, max_retries=0))
        with context as llm:
            options = {}
            if settings.groq_model in {"openai/gpt-oss-120b", "openai/gpt-oss-20b"}:
                options = {"reasoning_effort": settings.reasoning_effort,
                           "include_reasoning": False}
            elif settings.groq_model == "qwen/qwen3.8-27b":
                options = {"reasoning_effort": settings.reasoning_effort,
                           "reasoning_format": "hidden"}
            if tools:
                options.update(tools=tools, tool_choice=tool_choice)
            if response_format is not None:
                options["response_format"] = response_format
            raw_response = llm.chat.completions.with_raw_response.create(
                model=settings.groq_model,
                messages=messages,
                max_completion_tokens=token_limit,
                **options,
            )
            response = raw_response.parse()
            # Apenas três valores normalizados saem desta camada; cabeçalhos
            # livres, identificadores de requisição e corpo bruto não são expostos.
            rate_remaining_tokens = _nonnegative_header_int(
                raw_response.headers.get("x-ratelimit-remaining-tokens"),
            )
            rate_reset_seconds = _reset_seconds(
                raw_response.headers.get("x-ratelimit-reset-tokens"),
            )
            rate_limit_tokens = _nonnegative_header_int(
                raw_response.headers.get("x-ratelimit-limit-tokens"),
            )
        usage = response.usage
        choice = response.choices[0] if response.choices else None
        calls = _tool_calls(choice.message) if choice else []
        if choice and choice.finish_reason == "tool_calls" and not calls:
            raise GenerationError("O Groq encerrou o turno sem fornecer a chamada de ferramenta solicitada.")
        # completion_tokens já inclui raciocínio; não somar novamente.
        return GenerationTurn(
            text=normalize_citations(choice.message.content or "").strip() if choice else "",
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
            finish_reason=choice.finish_reason if choice else None,
            tool_calls=calls,
            rate_remaining_tokens=rate_remaining_tokens,
            rate_reset_seconds=rate_reset_seconds,
            rate_limit_tokens=rate_limit_tokens,
        )
    except groq.APITimeoutError:
        raise GenerationError(
            "O Groq não respondeu no tempo configurado. Tente novamente ou ajuste GROQ_TIMEOUT_SECONDS."
        ) from None
    except groq.APIConnectionError:
        raise GenerationError("Não foi possível conectar ao Groq. Confira sua conexão e tente novamente.") from None
    except groq.APIStatusError as exc:
        code = exc.status_code
        body = exc.body if isinstance(exc.body, dict) else {}
        error = body.get("error", {})
        provider_code = error.get("code") if isinstance(error, dict) else None
        allowed_codes = {"json_validate_failed", "tool_use_failed", "context_length_exceeded",
                         "model_decommissioned", "model_not_found", "rate_limit_exceeded"}
        provider_code = provider_code if provider_code in allowed_codes else None
        message = {
            400: "O Groq recusou a solicitação. Confira GROQ_MODEL e os limites configurados no .env.",
            401: "A chave Groq não foi aceita. Confira GROQ_API_KEY no .env.",
            403: "A conta Groq não tem permissão para usar este recurso ou modelo.",
            404: "O modelo não está disponível. Confira GROQ_MODEL e o acesso da sua conta Groq.",
            413: "A solicitação excedeu o tamanho aceito pelo Groq. Reduza o contexto enviado.",
            422: "O Groq não conseguiu processar a solicitação. Confira o modelo e seus parâmetros.",
            429: "O limite de uso do Groq foi atingido. Confira os limites no painel e aguarde antes de tentar novamente.",
        }.get(code, "O Groq está indisponível ou retornou um erro. Tente novamente mais tarde.")
        if provider_code == "json_validate_failed":
            message = "O modelo não conseguiu produzir os dados estruturados esperados. A ficha foi preservada."
        elif provider_code == "context_length_exceeded":
            message = "O contexto excedeu o limite aceito pelo modelo. A ficha foi preservada."
        if code == 429 and isinstance(error, dict):
            detail = str(error.get("message", "")).casefold()
            if "tokens per day" in detail or "tokens per day (tpd)" in detail:
                message = "A cota diária de tokens do Groq foi atingida. Aguarde a renovação no painel; a ficha foi preservada."
            elif "tokens per minute" in detail or "tokens per minute (tpm)" in detail:
                message = "A cota de tokens por minuto do Groq foi atingida. Aguarde antes de enviar outra mensagem; a ficha foi preservada."
        raise GenerationError(message, code, provider_code) from None


def generate_reply(settings: Settings, instructions: str, prompt: str,
                   client: groq.Groq | None = None) -> Generation:
    """Interface compatível para uma resposta sem ferramentas."""

    result = generate_turn(
        settings,
        [{"role": "system", "content": instructions},
         {"role": "user", "content": prompt}],
        client=client,
    )
    return Generation(
        text=result.text, input_tokens=result.input_tokens,
        output_tokens=result.output_tokens, finish_reason=result.finish_reason,
    )
