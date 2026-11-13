import json

import models

CHARS_PER_TOKEN = 3
MESSAGE_OVERHEAD_TOKENS = 8
IMAGE_TOKENS = 768
WINDOW_MARGIN_TOKENS = 512
LEFT_OUT_NOTE = "(Some earlier messages in this conversation were left out to fit the context window.)"
LEFT_OUT_ASK = ("\n\n(Earlier parts of this conversation were left out to fit. If the answer to this was in them, say you can "
                "no longer see that part of the conversation; don't guess.)")


def text_tokens(text: str) -> int:
    return -(-len(text) // CHARS_PER_TOKEN)


def message_tokens(message: dict) -> int:
    images = message.get("images") or []
    rest = {key: value for key, value in message.items() if key != "images"}
    return MESSAGE_OVERHEAD_TOKENS + text_tokens(json.dumps(rest, ensure_ascii=False)) + IMAGE_TOKENS * len(images)


def blocks_tokens(blocks: list[list[dict]]) -> int:
    return sum(message_tokens(message) for block in blocks for message in block)


def reply_room(body: dict) -> int:
    asked = (body.get("options") or {}).get("num_predict")
    return asked if isinstance(asked, int) and asked > 0 else models.DEFAULT_REPLY_TOKENS


def prompt_budget(config: dict, body: dict) -> int:
    tools = text_tokens(json.dumps(body.get("tools") or [], ensure_ascii=False))
    return models.context_tokens(config, body.get("model", "")) - reply_room(body) - tools - WINDOW_MARGIN_TOKENS


def turn_blocks(messages: list[dict]) -> list[list[dict]]:
    blocks = []
    for message in messages:
        answers_last_call = message.get("role") == "tool" and bool(blocks) and bool(blocks[-1][0].get("tool_calls"))
        if answers_last_call:
            blocks[-1].append(message)
        else:
            blocks.append([message])
    return blocks


def newest_that_fit(blocks: list[list[dict]], room: int) -> list[list[dict]]:
    kept = []
    for block in reversed(blocks):
        size = blocks_tokens([block])
        if size > room:
            break
        kept.insert(0, block)
        room -= size
    return kept


def latest_user_block(blocks: list[list[dict]]) -> int:
    return max((index for index, block in enumerate(blocks) if block[0].get("role") == "user"), default=len(blocks) - 1)


def with_left_out_ask(block: list[dict]) -> list[dict]:
    request = block[0]
    if not isinstance(request.get("content"), str):
        return block
    return [{**request, "content": request["content"] + LEFT_OUT_ASK}, *block[1:]]


def fitted_messages(messages: list[dict], budget: int) -> tuple[list[dict], int]:
    pinned = [message for message in messages if message.get("role") == "system"]
    blocks = turn_blocks([message for message in messages if message.get("role") != "system"])
    if not blocks or blocks_tokens([pinned] + blocks) <= budget:
        return messages, 0
    anchor = latest_user_block(blocks)
    note = {"role": "user", "content": LEFT_OUT_NOTE}
    room = budget - blocks_tokens([pinned, [note], blocks[anchor]])
    newer = newest_that_fit(blocks[anchor + 1:], room)
    older = newest_that_fit(blocks[:anchor], room - blocks_tokens(newer))
    kept = older + [with_left_out_ask(blocks[anchor])] + newer
    left_out = sum(len(block) for block in blocks) - sum(len(block) for block in kept)
    if not left_out:
        return messages, 0
    return pinned + [note] + [message for block in kept for message in block], left_out


def fit_to_window(config: dict, body: dict) -> tuple[dict, int]:
    messages, left_out = fitted_messages(body.get("messages") or [], prompt_budget(config, body))
    if not left_out:
        return body, 0
    return {**body, "messages": messages}, left_out
