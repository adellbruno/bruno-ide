# -*- coding: utf-8 -*-
"""Tradução entre o formato da Anthropic (Claude Code) e o da OpenAI (demais provedores)."""
import json
import re
import time
import uuid

STOP_MAP = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use",
            "function_call": "tool_use", "content_filter": "end_turn"}
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


class BadResponse(Exception):
    """Resposta vazia ou inutilizável: o roteador tenta o próximo modelo."""


# --------------------------------------------------------------- JSON de ferramentas
def _closers(s):
    """Devolve (fechamentos que faltam, terminou dentro de texto?)."""
    stack, in_str, esc = [], False, False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack and ((ch == "}" and stack[-1] == "{") or (ch == "]" and stack[-1] == "[")):
                stack.pop()
    return "".join("}" if c == "{" else "]" for c in reversed(stack)), in_str


def repair_json(s):
    """Conserta só problemas de ESTRUTURA (nunca inventa nem corta valores).

    Devolve (objeto, foi_consertado). Se o texto foi cortado no meio de um valor,
    devolve (None, False): é melhor tentar outro modelo do que executar um comando pela metade.
    """
    if s is None:
        return {}, False
    s = s.strip()
    if not s:
        return {}, False
    try:
        return json.loads(s), False
    except ValueError:
        pass
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", s).strip()
    try:
        return json.loads(t), True
    except ValueError:
        pass
    try:  # lixo depois do JSON (chaves sobrando, texto)
        obj, _end = json.JSONDecoder().raw_decode(t)
        return obj, True
    except ValueError:
        pass
    closers, in_str = _closers(t)
    if not in_str:
        cand = re.sub(r",\s*$", "", t)
        if re.search(r'(["\d\]}el])\s*$', cand):  # termina em um valor completo
            try:
                return json.loads(cand + _closers(cand)[0]), True
            except ValueError:
                pass
        cand2 = re.sub(r",(\s*[}\]])", r"\1", cand)
        try:
            return json.loads(cand2 + _closers(cand2)[0]), True
        except ValueError:
            pass
    if '"' not in t and "'" in t:
        try:
            return json.loads(t.replace("'", '"')), True
        except ValueError:
            pass
    return None, False


# ----------------------------------------------------------------------- utilidades
def text_of(content):
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def new_tool_id():
    return "toolu_" + uuid.uuid4().hex[:20]


def sse(ev, data):
    return f"event: {ev}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ------------------------------------------------- Anthropic -> OpenAI (pedido)
DROP = {"$schema", "$id", "additionalProperties", "propertyNames", "exclusiveMinimum",
        "exclusiveMaximum", "format"}


def clean_schema(s):
    if isinstance(s, list):
        return [clean_schema(x) for x in s]
    if not isinstance(s, dict):
        return s
    out = {}
    for k, v in s.items():
        if k in DROP:
            continue
        if k == "properties" and isinstance(v, dict):
            out[k] = {pk: clean_schema(pv) for pk, pv in v.items()}
        else:
            out[k] = clean_schema(v)
    return out


def tool_result_text(b):
    c = b.get("content")
    t = c if isinstance(c, str) else text_of(c or [])
    t = t or "(vazio)"
    return ("ERRO: " + t) if b.get("is_error") else t


def convert_messages(body):
    out = []
    if body.get("system"):
        out.append({"role": "system", "content": text_of(body["system"])})
    for m in body.get("messages", []):
        role, c = m["role"], m["content"]
        if isinstance(c, str):
            out.append({"role": role, "content": c})
            continue
        if role == "user":
            parts, tools = [], []
            for b in c:
                t = b.get("type")
                if t == "text":
                    parts.append({"type": "text", "text": b["text"]})
                elif t == "image":
                    s = b.get("source", {})
                    url = (f"data:{s.get('media_type')};base64,{s.get('data')}"
                           if s.get("type") == "base64" else s.get("url"))
                    if url:
                        parts.append({"type": "image_url", "image_url": {"url": url}})
                elif t == "tool_result":
                    tools.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                                  "content": tool_result_text(b)})
            out.extend(tools)
            if parts:
                if all(p["type"] == "text" for p in parts):
                    out.append({"role": "user", "content": "\n".join(p["text"] for p in parts)})
                else:
                    out.append({"role": "user", "content": parts})
        else:
            text = "".join(b.get("text", "") for b in c if b.get("type") == "text")
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"],
                                   "arguments": json.dumps(b.get("input", {}), ensure_ascii=False)}}
                     for b in c if b.get("type") == "tool_use"]
            msg = {"role": "assistant", "content": text if (text or not calls) else None}
            if calls:
                msg["tool_calls"] = calls
            if text or calls:
                out.append(msg)
    return out


def to_openai(body, cand, stream, include_usage=True):
    p = {"model": cand["modelo"], "messages": convert_messages(body), "stream": stream,
         "max_tokens": min(body.get("max_tokens", 4096), cand.get("max_tokens", 8192))}
    if stream and include_usage:
        p["stream_options"] = {"include_usage": True}
    for k in ("temperature", "top_p"):
        if k in body:
            p[k] = body[k]
    if body.get("stop_sequences"):
        p["stop"] = body["stop_sequences"][:4]
    if body.get("tools"):
        p["tools"] = [{"type": "function", "function": {
            "name": t["name"], "description": t.get("description", ""),
            "parameters": clean_schema(t.get("input_schema") or {"type": "object", "properties": {}})}}
            for t in body["tools"]]
        tc = body.get("tool_choice") or {}
        if tc.get("type") == "any":
            p["tool_choice"] = "required"
        elif tc.get("type") == "tool":
            p["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
        elif tc.get("type") == "none":
            p["tool_choice"] = "none"
    return p


# ------------------------------------------ OpenAI -> Anthropic (resposta completa)
def to_anthropic(data, model, est_in, info=None):
    """Converte a resposta sem streaming. Levanta BadResponse se for vazia/quebrada."""
    info = info if info is not None else {}
    if not data.get("choices"):
        raise BadResponse("resposta sem 'choices'")
    ch = data["choices"][0]
    msg = ch.get("message") or {}
    finish = ch.get("finish_reason")
    content = []
    if msg.get("content"):
        content.append({"type": "text", "text": msg["content"]})
    for tc in msg.get("tool_calls") or []:
        f = tc.get("function") or {}
        raw = f.get("arguments")
        if isinstance(raw, dict):
            inp, fixed = raw, False
        else:
            inp, fixed = repair_json(raw)
        if inp is None or finish == "length":
            info["bad_tool"] = info.get("bad_tool", 0) + 1
            raise BadResponse("argumentos de ferramenta incompletos")
        if fixed:
            info["repaired"] = info.get("repaired", 0) + 1
        content.append({"type": "tool_use", "id": tc.get("id") or new_tool_id(),
                        "name": f.get("name", ""), "input": inp})
    if not content:
        raise BadResponse("resposta vazia")
    stop = STOP_MAP.get(finish, "end_turn")
    if any(b["type"] == "tool_use" for b in content) and stop != "max_tokens":
        stop = "tool_use"
    u = data.get("usage") or {}
    info["usage"] = u
    return {"id": "msg_" + uuid.uuid4().hex[:24], "type": "message", "role": "assistant",
            "model": model, "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": u.get("prompt_tokens", est_in),
                      "output_tokens": u.get("completion_tokens", 1)}}


# ------------------------------------------------------------------- streaming
def _delta_of(obj):
    ch = (obj.get("choices") or [None])[0]
    return (ch or {}).get("delta") or {}, (ch or {}).get("finish_reason")


async def prime_stream(lines):
    """Lê o início do stream para descobrir cedo se a resposta é vazia ou erro.

    Devolve (linhas_já_lidas, iterador_do_resto). Levanta BadResponse se não há conteúdo.
    """
    it = lines.__aiter__()
    buf = []
    async for line in it:
        buf.append(line)
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            raise BadResponse("stream terminou sem conteúdo")
        try:
            obj = json.loads(data)
        except ValueError:
            continue
        if obj.get("error") and not obj.get("choices"):
            raise BadResponse("erro no stream: " + str(obj["error"])[:200])
        d, fin = _delta_of(obj)
        alive = (d.get("content") or d.get("reasoning_content") or d.get("reasoning")
                 or d.get("tool_calls"))
        if alive:
            return buf, it
        if fin:
            raise BadResponse("resposta vazia")
    raise BadResponse("stream terminou sem conteúdo")


async def _chain(buf, it):
    for line in buf:
        yield line
    async for line in it:
        yield line


async def translate_stream(lines, model, est_in, info):
    """Converte o stream da OpenAI em eventos SSE da Anthropic.

    Texto sai na hora. Chamadas de ferramenta ficam guardadas até o fim para que o JSON dos
    argumentos seja validado e, se preciso, consertado antes de chegar ao Claude Code.
    """
    yield sse("message_start", {"type": "message_start", "message": {
        "id": "msg_" + uuid.uuid4().hex[:24], "type": "message", "role": "assistant",
        "model": model, "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": est_in, "output_tokens": 1}}})
    idx, text_open = -1, False
    calls, order, id_to_i = {}, [], {}
    out_chars, finish, usage = 0, None, None
    last_ping = 0.0
    failed = None
    try:
        async for line in lines:
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            if obj.get("usage"):
                usage = obj["usage"]
            if obj.get("error") and not obj.get("choices"):
                raise RuntimeError(str(obj["error"])[:300])
            d, fin = _delta_of(obj)
            txt = d.get("content")
            if txt and isinstance(txt, str):
                if not text_open:
                    idx += 1
                    text_open = True
                    yield sse("content_block_start", {"type": "content_block_start", "index": idx,
                                                      "content_block": {"type": "text", "text": ""}})
                yield sse("content_block_delta", {"type": "content_block_delta", "index": idx,
                                                  "delta": {"type": "text_delta", "text": txt}})
                out_chars += len(txt)
            elif (d.get("reasoning_content") or d.get("reasoning")) and time.time() - last_ping > 5:
                last_ping = time.time()  # modelo "pensando": mantém a conexão viva
                yield sse("ping", {"type": "ping"})
            for tc in d.get("tool_calls") or []:
                f = tc.get("function") or {}
                i = tc.get("index")
                tid = tc.get("id")
                if i is None:
                    if tid and tid not in id_to_i:
                        i = len(order)
                    else:
                        i = order[-1] if order else 0
                if i not in calls:
                    calls[i] = {"id": tid or new_tool_id(), "name": "", "args": ""}
                    order.append(i)
                if tid:
                    id_to_i[tid] = i
                    calls[i]["id"] = tid
                if f.get("name") and not calls[i]["name"]:
                    calls[i]["name"] = f["name"]
                if f.get("arguments"):
                    a = f["arguments"]
                    a = a if isinstance(a, str) else json.dumps(a)
                    calls[i]["args"] += a
                    out_chars += len(a)
            if fin:
                finish = fin
    except Exception as e:
        failed = e
    finally:
        close = getattr(lines, "aclose", None)
        if close:
            try:
                await close()
            except Exception:
                pass

    if failed is not None:
        info["error"] = f"{type(failed).__name__}: {failed}"
        if text_open:
            yield sse("content_block_stop", {"type": "content_block_stop", "index": idx})
        yield sse("error", {"type": "error", "error": {
            "type": "overloaded_error", "message": f"Falha do provedor durante a resposta: {failed}"}})
        return

    if text_open:
        yield sse("content_block_stop", {"type": "content_block_stop", "index": idx})
    bad = False
    for i in order:
        c = calls[i]
        inp, fixed = repair_json(c["args"])
        if inp is None or finish == "length":
            bad = True
            break
        if fixed:
            info["repaired"] = info.get("repaired", 0) + 1
        c["input"] = inp
    if bad:
        info["bad_tool"] = info.get("bad_tool", 0) + 1
        info["error"] = "argumentos de ferramenta incompletos"
        yield sse("error", {"type": "error", "error": {
            "type": "overloaded_error",
            "message": "O modelo devolveu uma chamada de ferramenta incompleta; tentando outro modelo."}})
        return
    for i in order:
        c = calls[i]
        idx += 1
        yield sse("content_block_start", {"type": "content_block_start", "index": idx, "content_block": {
            "type": "tool_use", "id": c["id"], "name": c["name"], "input": {}}})
        yield sse("content_block_delta", {"type": "content_block_delta", "index": idx, "delta": {
            "type": "input_json_delta", "partial_json": json.dumps(c["input"], ensure_ascii=False)}})
        yield sse("content_block_stop", {"type": "content_block_stop", "index": idx})
    if idx < 0:  # nada saiu (não deveria ocorrer após o prime)
        yield sse("content_block_start", {"type": "content_block_start", "index": 0,
                                          "content_block": {"type": "text", "text": ""}})
        yield sse("content_block_stop", {"type": "content_block_stop", "index": 0})
    reason = STOP_MAP.get(finish, "end_turn")
    if order and reason != "max_tokens":
        reason = "tool_use"
    info["usage"] = usage
    out_tokens = (usage or {}).get("completion_tokens") or max(1, out_chars // 3)
    yield sse("message_delta", {"type": "message_delta",
                                "delta": {"stop_reason": reason, "stop_sequence": None},
                                "usage": {"output_tokens": out_tokens}})
    yield sse("message_stop", {"type": "message_stop"})


def stream_lines(resp):
    return resp.aiter_lines()
