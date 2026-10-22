# Kimi Customer Support Agent

A customer support chat prototype using Kimi and a local file-based knowledge base. The model answers from retrieved documents and can suggest human support when information is missing. The human handoff button emits a browser event; it does not connect to a support service by itself.

## Getting started

From the repository root, copy `.env.kimi.example` to `.env.kimi.local` and set `KIMI_API_KEY`. Then run:

```bash
./scripts/kimi-run customer-support-agent npm ci
./scripts/kimi-run customer-support-agent npm run dev
```

Open http://localhost:3000. Alternatively, copy `.env.example` to `.env.local` in this directory, set the key, and run `npm ci` and `npm run dev` here.

## Local knowledge base

Add UTF-8 Markdown (`.md`) or text (`.txt`) documents to the `knowledge/` directory. Use short, clearly titled sections and include the product facts you want the assistant to answer from. Files are read locally for retrieval; relevant excerpts are sent to Kimi with the user's question. Do not put secrets or sensitive information in the knowledge base unless sending them to the configured model service is appropriate.

Restart the server after adding or editing documents, because the knowledge base is cached in memory. The included document is illustrative; review and replace it with verified, current product information before use. This is a lightweight keyword-based demo, so a question may not match a document that uses very different wording. When no relevant information is found, the assistant should say it cannot verify the answer.

The knowledge base selector represents the local knowledge directory. No AWS account or credentials are needed. The knowledge files should be present in the application working directory when deployed.

## Layout scripts

`npm run dev` includes both sidebars. Use `npm run dev:left`, `npm run dev:right`, or `npm run dev:chat` for other layouts; corresponding `build:*` scripts are also available.

This is a prototype, not a production-ready customer support service. See the repository [LICENSE](../LICENSE).
