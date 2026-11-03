# datachat-agent (JavaScript)

Client for [DataChat Agent](https://github.com/Ashish-devl/Data-chat-agent): ask your SQL
database and PDFs questions in plain English. Works in browsers and Node 18+, no dependencies.

```bash
npm install datachat-agent
```

```js
import { DataChatClient } from "datachat-agent";

const dc = new DataChatClient({ baseUrl: "https://api.example.com", apiKey: "dc_live_..." });

const a = await dc.ask("Which production orders are delayed?", { sessionId: "chat-1" });
console.log(a.answer, a.sql, a.rows, a.citations);

// The answer as it is written
for await (const ev of dc.askStream("And which buyers are they for?", { sessionId: "chat-1" })) {
  if (ev.type === "token") process.stdout.write(ev.text);
  if (ev.type === "done") console.log("\nsources:", ev.response.citations);
}

await dc.feedback(a.query_id, "helpful");
```

In a web page, use a **publishable** key (`dc_pub_...`), which only asks questions and only works
on the websites it was created for. Never put a secret key (`dc_live_...`) in a browser.

Errors are thrown as `DataChatError` with `status`, `detail` and `retryAfter` (seconds, for
rate limits).

| Method | Does |
| --- | --- |
| `ask(question, options)` | Whole answer: text, SQL, rows, citations, chart hint |
| `askStream(question, options)` | Events: `meta`, `data`, `token`, `done`, `error` |
| `feedback(queryId, "helpful" \| "wrong", comment?)` | Rate an answer |
| `session(sessionId)` | A conversation's turns (secret keys) |
| `uploadDocument(blob, filename)` / `listDocuments()` | PDFs (needs the `docs` scope) |
| `usage()` | Requests and tokens this month (admin keys) |

Options for `ask`/`askStream`: `connectionId`, `sessionId`, `userRef`, `userContext` (row
filters; secret keys only), `includeRows`, `signal`.

MIT licence.
