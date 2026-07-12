# AIDream API Endpoints

> **Cloud server API** (AIDream), not the local matrx engine. Used by desktop UI via direct HTTPS. Index: [ARCHITECTURE.md](../official/ARCHITECTURE.md).

Base URL: `https://<host>/api`

All authenticated endpoints require an `Authorization: Bearer <jwt>` header.

---

## Prompts

### Public

#### `GET /api/prompts/builtins`
Returns all prompt builtins. No auth required.

**Response**
```json
{
  "builtins": [ { ...PromptBuiltins } ],
  "count": 42
}
```

### Authenticated (JWT required)

#### `GET /api/prompts`
Returns all prompts belonging to the authenticated user.

**Response**
```json
{
  "prompts": [ { ...Prompts } ],
  "count": 5
}
```

#### `GET /api/prompts/all`
Returns the authenticated user's prompts **and** all prompt builtins in a single call.

**Response**
```json
{
  "prompts": [ { ...Prompts } ],
  "builtins": [ { ...PromptBuiltins } ],
  "total_count": 47
}
```

---

## Conversations (CX Data)

All endpoints require a valid JWT.

#### `GET /api/cx/conversations`
Returns all conversations for the authenticated user.

**Response**
```json
{
  "conversations": [ { ...CxConversation } ],
  "count": 12
}
```

#### `GET /api/cx/conversations/{conversation_id}`
Returns a single conversation with all related data (messages, requests, tool calls, etc.).

**Path params**
| Param | Type | Description |
|-------|------|-------------|
| `conversation_id` | `string (UUID)` | ID of the conversation |

**Response**
```json
{
  "conversation": { ...CxConversation },
  "related": { ...all related records }
}
```

**Errors**
- `404` — conversation not found

#### `GET /api/cx/conversations/{conversation_id}/requests`
Returns all `CxRequest` records for a specific conversation.

**Path params**
| Param | Type | Description |
|-------|------|-------------|
| `conversation_id` | `string (UUID)` | ID of the conversation |

**Response**
```json
{
  "requests": [ { ...CxRequest } ],
  "count": 8
}
```

---

## AI Models

Public — no auth required.

#### `GET /api/ai-models`
Returns all AI models available in the system.

**Response**
```json
{
  "models": [ { ...AiModel } ],
  "count": 87
}
```

---

## AI Tools

Public — no auth required.

#### `GET /api/ai-tools`
Returns all registered tools.

**Response**
```json
{
  "tools": [ { ...Tool } ],
  "count": 60
}
```

#### `GET /api/ai-tools/app/{source_app}`
Returns tools filtered by `source_app`.

**Path params**
| Param | Type | Description |
|-------|------|-------------|
| `source_app` | `string` | App identifier (e.g. `matrx_ai`, `matrx_local`) |

**Response**
```json
{
  "tools": [ { ...Tool } ],
  "count": 30,
  "source_app": "matrx_ai"
}
```

#### `GET /api/ai-tools/app/matrx_ai/all`
Convenience shortcut — returns all tools for the `matrx_ai` app.

#### `GET /api/ai-tools/app/matrx_local/all`
Convenience shortcut — returns all tools for the `matrx_local` app.

#### `GET /api/ai-tools/{tool_id}`
Returns a single tool by ID with all related records.

**Path params**
| Param | Type | Description |
|-------|------|-------------|
| `tool_id` | `string (UUID)` | ID of the tool |

**Response**
```json
{
  "tool": { ...Tool },
  "related": { ...all related records }
}
```

**Errors**
- `404` — tool not found

---

## Source Files

| Router file | Prefix | Auth |
|-------------|--------|------|
| `aidream/api/routers/prompts.py` | `/prompts` | public (builtins) + authenticated (user prompts) |
| `aidream/api/routers/cx_data.py` | `/cx` | authenticated |
| `aidream/api/routers/ai_models.py` | `/ai-models` | public |
| `aidream/api/routers/ai_tools.py` | `/ai-tools` | public |
