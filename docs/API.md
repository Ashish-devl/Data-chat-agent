# API reference

Generated from the server's OpenAPI description by `scripts/api_reference.py`; a running
server also serves it interactively at `/docs`. Every path starts with `/v1` and every
request except `/v1/health` and `/v1/auth/login` sends `Authorization: Bearer <key or
console session token>`. Errors come back as `{"detail": "..."}` with a matching
status: 401 (no or bad key), 403 (missing scope), 404, 409, 422 (invalid input),
429 (rate or token limit; see `Retry-After`), 503 (the model is unavailable).

Scopes: `ask` (questions, feedback), `docs` (PDFs), `admin` (everything). Publishable
keys (`dc_pub_`) are ask-only and work only from their allowed websites. Console owners
act as `admin`, members as `ask` + `docs`.

| Group | Scope | Endpoints |
| --- | --- | --- |
| Ask | ask | `POST /v1/ask`, `POST /v1/ask/stream`, `GET /v1/sessions/{session_id}` |
| Feedback | ask | `POST /v1/feedback` |
| Documents | docs | `GET /v1/documents`, `POST /v1/documents`, `GET /v1/documents/{document_id}`, `DELETE /v1/documents/{document_id}`, `GET /v1/documents/{document_id}/file`, `GET /v1/documents/{document_id}/items`, `POST /v1/documents/{document_id}/reprocess` |
| Connections | admin | `GET /v1/connections`, `POST /v1/connections`, `GET /v1/connections/{connection_id}`, `PATCH /v1/connections/{connection_id}`, `DELETE /v1/connections/{connection_id}`, `POST /v1/connections/{connection_id}/test`, `POST /v1/connections/{connection_id}/scan`, `GET /v1/connections/{connection_id}/schema` |
| Semantic layer | admin | `PATCH /v1/schema-items/{item_id}`, `GET /v1/connections/{connection_id}/terms`, `POST /v1/connections/{connection_id}/terms`, `PATCH /v1/terms/{term_id}`, `DELETE /v1/terms/{term_id}`, `GET /v1/connections/{connection_id}/examples`, `POST /v1/connections/{connection_id}/examples`, `DELETE /v1/examples/{example_id}` |
| Keys | admin | `GET /v1/keys`, `POST /v1/keys`, `DELETE /v1/keys/{key_id}` |
| Usage | admin | `GET /v1/usage` |
| Console | see notes | `POST /v1/auth/login`, `POST /v1/auth/logout`, `GET /v1/auth/me`, `POST /v1/auth/password`, `GET /v1/users`, `POST /v1/users`, `PATCH /v1/users/{user_id}`, `DELETE /v1/users/{user_id}`, `GET /v1/query-log` |
| Health | none | `GET /v1/health` |

## Ask

### `POST /v1/ask`

Ask a question. The answer comes from the database, the documents or both, with the SQL, rows, citations and a chart hint. Returns 503 with `error` set when the model is unavailable.

Body (application/json): [`AskRequest`](#askrequest)
Returns 200: [AskResponse](#askresponse)

### `POST /v1/ask/stream`

Same as /ask, as Server-Sent Events: `meta`, then `data` (SQL and rows) if any, then `token` events with the answer as it is written, then `done` with the full /ask response.

Body (application/json): [`AskRequest`](#askrequest)
Returns 200: no body

### `GET /v1/sessions/{session_id}`

The turns of a conversation (secret keys and console users).

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `session_id` | path | string |  |

Returns 200: [SessionOut](#sessionout)

## Feedback

### `POST /v1/feedback`

Mark an answer helpful or wrong. Sending again replaces the earlier rating.

Body (application/json): [`FeedbackIn`](#feedbackin)
Returns 200: [FeedbackOut](#feedbackout)

## Documents

### `GET /v1/documents`

Uploaded PDFs, newest first, with processing status and counts.

Returns 200: [list of DocumentOut](#documentout)

### `POST /v1/documents`

Upload a PDF. The same file uploaded twice returns the existing document (200).

Body (multipart/form-data): [`Body_upload_document_v1_documents_post`](#body_upload_document_v1_documents_post)
Returns 201: [DocumentOut](#documentout)

### `GET /v1/documents/{document_id}`

One document's status, page count, printed date and counts.

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `document_id` | path | uuid |  |

Returns 200: [DocumentOut](#documentout)

### `DELETE /v1/documents/{document_id}`

Removes the PDF, its passages and its line items.

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `document_id` | path | uuid |  |

Returns 204: no body

### `GET /v1/documents/{document_id}/file`

The original PDF, byte for byte.

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `document_id` | path | uuid |  |

Returns 200: no body

### `GET /v1/documents/{document_id}/items`

Line items (item, qty, unit, rate, amount) taken from the document's tables.

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `document_id` | path | uuid |  |

Returns 200: [list of ItemOut](#itemout)

### `POST /v1/documents/{document_id}/reprocess`

Run extraction again, e.g. after a failure or after configuring a model.

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `document_id` | path | uuid |  |

Returns 202: [DocumentOut](#documentout)

## Connections

### `GET /v1/connections`

Connected databases, oldest first. Passwords are never returned.

Returns 200: [list of ConnectionOut](#connectionout)

### `POST /v1/connections`

Register a database. The dialect comes from the connection string; the string is encrypted at rest. Test it next.

Body (application/json): [`ConnectionCreate`](#connectioncreate)
Returns 201: [ConnectionOut](#connectionout)

### `GET /v1/connections/{connection_id}`

One connected database.

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `connection_id` | path | uuid |  |

Returns 200: [ConnectionOut](#connectionout)

### `PATCH /v1/connections/{connection_id}`

Rename it, change the connection string (it must be tested again), the schema allowlist or the row filters.

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `connection_id` | path | uuid |  |

Body (application/json): [`ConnectionUpdate`](#connectionupdate)
Returns 200: [ConnectionOut](#connectionout)

### `DELETE /v1/connections/{connection_id}`

Remove a database and everything learned about it (schema, terms, examples).

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `connection_id` | path | uuid |  |

Returns 204: no body

### `POST /v1/connections/{connection_id}/test`

Connect and check the user can only read.  A user that can write is refused (`status: rejected`).

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `connection_id` | path | uuid |  |

Returns 200: [ConnectionOut](#connectionout)

### `POST /v1/connections/{connection_id}/scan`

Read the schema now (default), or with `?wait=false` in the background (202).

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `connection_id` | path | uuid |  |
| `wait` | query | boolean |  |

Returns 200: [ScanResult](#scanresult)

### `GET /v1/connections/{connection_id}/schema`

Tables and columns as last scanned, with descriptions, sample values and hidden flags.

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `connection_id` | path | uuid |  |

Returns 200: [list of SchemaTableOut](#schematableout)

## Semantic layer

### `PATCH /v1/schema-items/{item_id}`

Describe a table or column, or hide it. Hidden items are never shown to the model and can never be queried.

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `item_id` | path | uuid |  |

Body (application/json): [`SchemaItemUpdate`](#schemaitemupdate)
Returns 200: [SchemaItemOut](#schemaitemout)

### `GET /v1/connections/{connection_id}/terms`

Business terms for this database.

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `connection_id` | path | uuid |  |

Returns 200: [list of TermOut](#termout)

### `POST /v1/connections/{connection_id}/terms`

Teach a business term, e.g. `open PO` = a purchase order whose status is Open or Partially Received.

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `connection_id` | path | uuid |  |

Body (application/json): [`TermIn`](#termin)
Returns 201: [TermOut](#termout)

### `PATCH /v1/terms/{term_id}`

Change a business term.

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `term_id` | path | uuid |  |

Body (application/json): [`TermUpdate`](#termupdate)
Returns 200: [TermOut](#termout)

### `DELETE /v1/terms/{term_id}`

Remove a business term.

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `term_id` | path | uuid |  |

Returns 204: no body

### `GET /v1/connections/{connection_id}/examples`

Worked question-to-SQL examples for this database.

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `connection_id` | path | uuid |  |

Returns 200: [list of ExampleOut](#exampleout)

### `POST /v1/connections/{connection_id}/examples`

Add a worked example. Its SQL must pass the same safety checks as any query; the 3 closest examples are shown to the model.

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `connection_id` | path | uuid |  |

Body (application/json): [`ExampleIn`](#examplein)
Returns 201: [ExampleOut](#exampleout)

### `DELETE /v1/examples/{example_id}`

Remove a worked example.

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `example_id` | path | uuid |  |

Returns 204: no body

## Keys

### `GET /v1/keys`

API keys of this account, newest first. The keys themselves are never shown again.

Returns 200: [list of KeyOut](#keyout)

### `POST /v1/keys`

Create a key. The full key is in this response only. A publishable key (type `publishable`) is ask-only and works only from `allowed_domains`.

Body (application/json): [`KeyCreate`](#keycreate)
Returns 201: [KeyCreated](#keycreated)

### `DELETE /v1/keys/{key_id}`

Revoke a key. Anything using it stops working at once.

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `key_id` | path | uuid |  |

Returns 204: no body

## Usage

### `GET /v1/usage`

Questions and model tokens this calendar month, with this account's limits.

Returns 200: [Usage](#usage)

## Console

### `POST /v1/auth/login`

Sign in to the console with email and password. Returns a session token valid for 12 hours. No key needed.

Body (application/json): [`LoginIn`](#loginin)
Returns 200: [LoginOut](#loginout)

### `POST /v1/auth/logout`

End this console session.

Returns 204: no body

### `GET /v1/auth/me`

The signed-in console user.

Returns 200: [Me](#me)

### `POST /v1/auth/password`

Change your password. Your other sessions are signed out.

Body (application/json): [`PasswordChange`](#passwordchange)
Returns 204: no body

### `GET /v1/users`

Console users of this account.

Returns 200: [list of UserOut](#userout)

### `POST /v1/users`

Add a console user (owner or member) with a password that meets the rules.

Body (application/json): [`UserCreate`](#usercreate)
Returns 201: [UserOut](#userout)

### `PATCH /v1/users/{user_id}`

Change a user's name or role, or disable them (which signs them out). At least one active owner is kept.

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `user_id` | path | uuid |  |

Body (application/json): [`UserUpdate`](#userupdate)
Returns 200: [UserOut](#userout)

### `DELETE /v1/users/{user_id}`

Remove a console user. You cannot remove yourself or the last owner.

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `user_id` | path | uuid |  |

Returns 204: no body

### `GET /v1/query-log`

Every question asked, newest first: what was asked, the SQL, the answer and its rating.

| Parameter | In | Type | Notes |
| --- | --- | --- | --- |
| `limit` | query | integer |  |
| `offset` | query | integer |  |
| `feedback` | query | `helpful` | `wrong` | `any` (optional) |  |
| `route` | query | string (optional) |  |
| `errors` | query | boolean |  |
| `q` | query | string (optional) | Search questions and answers |

Returns 200: [LogPage](#logpage)

## Health

### `GET /v1/health`

Liveness check: the server is up. No key needed.

Returns 200: object

## Models

### AskRequest

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `question` | string | yes |  |
| `connection_id` | uuid (optional) |  | Optional when the tenant has exactly one ready connection |
| `session_id` | string (optional) |  | Your id for the conversation; follow-ups with the same id see earlier turns |
| `user_ref` | string (optional) |  |  |
| `user_context` | object |  | Values for row filters, e.g. {"user_company": 7} |
| `include_rows` | boolean |  |  Default `True`. |

### AskResponse

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `query_id` | uuid | yes |  |
| `session_id` | string | yes |  |
| `answer` | string | yes |  |
| `route` | string | yes |  |
| `sql` | string (optional) | yes |  |
| `columns` | list of string |  |  |
| `rows` | list of object |  |  |
| `row_count` | integer |  |  Default `0`. |
| `truncated` | boolean |  |  Default `False`. |
| `citations` | list of object |  |  |
| `chart` | Chart (optional) |  |  |
| `latency_ms` | integer | yes |  |
| `tokens` | integer | yes |  |
| `error` | string (optional) |  |  |

### SessionOut

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `session_id` | string | yes |  |
| `user_ref` | string (optional) | yes |  |
| `created_at` | date-time | yes |  |
| `turns` | list of TurnOut | yes |  |

### FeedbackIn

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `query_id` | uuid | yes |  |
| `rating` | `helpful` | `wrong` | yes |  |
| `comment` | string (optional) |  |  |

### FeedbackOut

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `query_id` | uuid | yes |  |
| `rating` | string | yes |  |
| `comment` | string (optional) | yes |  |
| `recorded_at` | date-time | yes |  |

### DocumentOut

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | uuid | yes |  |
| `filename` | string | yes |  |
| `size_bytes` | integer | yes |  |
| `sha256` | string | yes |  |
| `pages` | integer (optional) | yes |  |
| `doc_date` | date (optional) | yes |  |
| `status` | string | yes |  |
| `error` | string (optional) | yes |  |
| `chunks` | integer |  |  Default `0`. |
| `items` | integer |  |  Default `0`. |
| `created_at` | date-time | yes |  |

### Body_upload_document_v1_documents_post

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `file` | string | yes | A PDF with a text layer, up to 20 MB |

### ItemOut

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | uuid | yes |  |
| `page` | integer | yes |  |
| `row_index` | integer | yes |  |
| `item` | string (optional) | yes |  |
| `qty` | number (optional) | yes |  |
| `unit` | string (optional) | yes |  |
| `rate` | number (optional) | yes |  |
| `amount` | number (optional) | yes |  |

### ConnectionOut

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | uuid | yes |  |
| `name` | string | yes |  |
| `dialect` | string | yes |  |
| `target` | string | yes |  |
| `schema_allowlist` | list of string | yes |  |
| `row_filters` | object | yes |  |
| `status` | string | yes |  |
| `status_detail` | string (optional) | yes |  |
| `last_scanned_at` | date-time (optional) | yes |  |
| `created_at` | date-time | yes |  |

### ConnectionCreate

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string | yes |  |
| `dsn` | string | yes | postgresql://user:pass@host/db or mysql://... |
| `schema_allowlist` | list of string |  |  |
| `row_filters` | object |  | Per-table SQL conditions, e.g. {"orders": "company_id = :user_company"} |

### ConnectionUpdate

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string (optional) |  |  |
| `dsn` | string (optional) |  |  |
| `schema_allowlist` | list of string (optional) |  |  |
| `row_filters` | object (optional) |  |  |

### ScanResult

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `connection` | ConnectionOut | yes |  |
| `tables` | integer |  |  Default `0`. |
| `columns` | integer |  |  Default `0`. |
| `removed` | integer |  |  Default `0`. |
| `queued` | boolean |  |  Default `False`. |

### SchemaTableOut

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | uuid | yes |  |
| `schema_name` | string | yes |  |
| `name` | string | yes |  |
| `kind` | string (optional) | yes |  |
| `row_count` | integer (optional) | yes |  |
| `description` | string (optional) | yes |  |
| `hidden` | boolean | yes |  |
| `columns` | list of SchemaColumnOut | yes |  |

### SchemaItemUpdate

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `description` | string (optional) |  |  |
| `hidden` | boolean (optional) |  |  |

### SchemaItemOut

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | uuid | yes |  |
| `schema_name` | string | yes |  |
| `table_name` | string | yes |  |
| `column_name` | string (optional) | yes |  |
| `description` | string (optional) | yes |  |
| `hidden` | boolean | yes |  |

### TermOut

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | uuid | yes |  |
| `connection_id` | uuid | yes |  |
| `term` | string | yes |  |
| `definition` | string | yes |  |
| `created_at` | date-time | yes |  |

### TermIn

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `term` | string | yes |  |
| `definition` | string | yes |  |

### TermUpdate

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `term` | string (optional) |  |  |
| `definition` | string (optional) |  |  |

### ExampleOut

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | uuid | yes |  |
| `connection_id` | uuid | yes |  |
| `question` | string | yes |  |
| `sql` | string | yes |  |
| `created_at` | date-time | yes |  |

### ExampleIn

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `question` | string | yes |  |
| `sql` | string | yes |  |

### KeyOut

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | uuid | yes |  |
| `name` | string | yes |  |
| `prefix` | string | yes |  |
| `scopes` | list of string | yes |  |
| `key_type` | string | yes |  |
| `allowed_domains` | list of string | yes |  |
| `revoked` | boolean | yes |  |
| `created_at` | date-time | yes |  |
| `last_used_at` | date-time (optional) | yes |  |

### KeyCreate

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string | yes |  |
| `scopes` | list of Scope |  |  Default `['ask']`. |
| `type` | `secret` | `publishable` |  | publishable: for a web page (the chat widget); ask-only, tied to websites Default `secret`. |
| `allowed_domains` | list of string |  | Websites a publishable key works on, e.g. ["erp.example.com", "*.x.com"] |

### KeyCreated

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | uuid | yes |  |
| `name` | string | yes |  |
| `prefix` | string | yes |  |
| `scopes` | list of string | yes |  |
| `key_type` | string | yes |  |
| `allowed_domains` | list of string | yes |  |
| `revoked` | boolean | yes |  |
| `created_at` | date-time | yes |  |
| `last_used_at` | date-time (optional) | yes |  |
| `key` | string | yes | The full key. Shown once; store it now. |

### Usage

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `period_start` | date-time | yes |  |
| `requests` | integer | yes |  |
| `tokens` | integer | yes |  |
| `monthly_token_limit` | integer (optional) | yes |  |
| `tokens_remaining` | integer (optional) | yes |  |
| `rate_limit_per_minute` | integer | yes |  |

### LoginIn

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `email` | string | yes |  |
| `password` | string | yes |  |

### LoginOut

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `token` | string | yes |  |
| `expires_at` | date-time | yes |  |
| `user` | Me | yes |  |

### Me

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | uuid | yes |  |
| `email` | string | yes |  |
| `name` | string (optional) | yes |  |
| `role` | string | yes |  |
| `disabled` | boolean | yes |  |
| `last_login_at` | date-time (optional) | yes |  |
| `created_at` | date-time | yes |  |
| `tenant_id` | uuid | yes |  |
| `tenant_name` | string | yes |  |

### PasswordChange

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `current_password` | string | yes |  |
| `new_password` | string | yes |  |

### UserOut

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | uuid | yes |  |
| `email` | string | yes |  |
| `name` | string (optional) | yes |  |
| `role` | string | yes |  |
| `disabled` | boolean | yes |  |
| `last_login_at` | date-time (optional) | yes |  |
| `created_at` | date-time | yes |  |

### UserCreate

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `email` | string | yes |  |
| `name` | string (optional) |  |  |
| `role` | `owner` | `member` |  |  Default `member`. |
| `password` | string | yes |  |

### UserUpdate

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string (optional) |  |  |
| `role` | `owner` | `member` (optional) |  |  |
| `disabled` | boolean (optional) |  |  |

### LogPage

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `total` | integer | yes |  |
| `items` | list of LogEntry | yes |  |
