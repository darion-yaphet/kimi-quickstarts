# Kimi Browser Tools Quickstart

This example gives Kimi Code five ordinary custom tools for a Chromium tab: `navigate`, `read_page`, `left_click`, `get_page_text`, and `wait`. It uses the Anthropic-compatible Messages API rather than a hosted browser-toolset protocol.

The existing SDK driver remains the local execution and safety layer. It drives one tab over the Chrome DevTools Protocol (CDP); the Kimi adapter declares ordinary JSON schemas and dispatches calls through the driver's validation and URL-policy pipeline.

| File | What it is |
| --- | --- |
| `run.py` / `run.ts` | Runs a bounded Kimi Messages API tool loop. Start here. |
| `kimi_adapter.py` / `kimi-adapter.ts` | Ordinary tool schemas, local dispatch, and the tool loop. |
| `exercise.py` / `exercise.ts` | Exercises the same dispatch path without a model against a local page. |
| `cdp_browser.py` / `cdp-browser.ts` | The example driver: a subclass of the SDK's abstract browser toolset, one Chromium tab over raw CDP. |

> [!WARNING]
> The pages the model visits affect what it does next. Run this against a throwaway browser profile inside a sandbox with egress controls, not against your everyday browser.
>
> Before you point it at anything else, read "Running a browser toolset safely" in the SDK's tool helper docs: [Python](https://github.com/anthropics/anthropic-sdk-python/blob/main/browser-toolset.md#running-a-browser-toolset-safely), [TypeScript](https://github.com/anthropics/anthropic-sdk-typescript/blob/main/browser-toolset.md#running-a-browser-toolset-safely).

## Install

You need:

- a Kimi Code API key in `ANTHROPIC_API_KEY`;
- an Anthropic SDK version with the browser toolset helpers (`anthropic.tools.browser` in Python, `@anthropic-ai/sdk/helpers/beta/toolsets` in TypeScript);
- Python 3.10+ or Node.js 20+;
- a Chromium binary, from `CHROME_PATH` or `google-chrome` / `chromium` on your `PATH`. Ubuntu's snap-packaged Chromium cannot open a profile under `/tmp`. Install Google Chrome's `.deb`, or point `CHROME_PATH` at a non-snap build.

```bash
cd browser-toolset/python
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

```bash
cd browser-toolset/typescript
npm install
```

## Run

```bash
python run.py "Open example.com and tell me the heading"          # Python
npm run start -- "Open example.com and tell me the heading"       # TypeScript
```

The whole command line is the task. `run.ts` reads no options, so even `--help` goes to the model. `run.py` answers `--help` itself.

It prints each model response: tool calls followed by the final answer. By default it sends standard Messages API requests to `https://api.kimi.com/coding/` with model `kimi-for-coding`.
Each response has an 8192-token output budget. A `max_tokens` stop is reported as an error instead of being mistaken for a completed task.

`run` passes the driver an example URL policy (`example_policy` in `run.py`, `examplePolicy` in `run.ts`) that admits only http(s) pages on the hosts in `ALLOWED_DOMAINS`, subdomains included, and the empty tab. Every Kimi `navigate` call goes through that policy before CDP receives it. It is not a production policy. The SDK guide linked above covers what yours needs.

A `confirm` hook, which asks a person before a tool runs, goes in the same place: the toolset's `confirm` option. The example driver enables no tool that needs one.

| Variable | Effect |
| --- | --- |
| `ALLOWED_DOMAINS` | Comma-separated hosts the model may visit, subdomains included. Default: `example.com,iana.org`. Set but empty, it admits no host. |
| `HEADLESS` | Set to `0` to watch the browser. Chromium opens with two tabs: its own blank start-up tab and the one the driver works in. |
| `CHROME_PATH` | A specific Chromium binary. |
| `ANTHROPIC_BASE_URL` | Anthropic-compatible endpoint. Default: `https://api.kimi.com/coding/`. |
| `MODEL` | Model to run. Default: `kimi-for-coding`. |
| `MAX_TOOL_TURNS` | Maximum model/tool round trips. Default: `20`. |

## Exercise the toolset without a model

`exercise` needs no API key. It serves a small page on localhost and launches the driver with a policy that admits only that page. Then it sends the calls a model would send through the Kimi adapter:

```bash
python exercise.py        # Python
npm run exercise          # TypeScript

python -m unittest -v test_kimi_adapter.py  # adapter regression tests
npm run typecheck                             # TypeScript adapter and driver types
```

It sends calls through the same Kimi dispatch adapter used by `run` and prints each `tool_result` as the model would see it. Four calls are answered: `navigate`, `read_page`, a `left_click` on a `ref_N` from the read, and `get_page_text`, which shows what the click did. Two are refused with `is_error`: a `navigate` to the same page by `127.0.0.1`, and a `wait` longer than 30 seconds. It exits non-zero if any call comes back differently.

## What the example driver implements, and what it leaves out

Implemented: `navigate` (to a URL; returns once the document is parsed), `read_page` (headings, links, buttons and form controls, each with a `ref_N`), `left_click` (by ref or by coordinate), `get_page_text` and `wait`, plus the driver's browser-state report. The Kimi adapter supplies the ordinary tool schemas and bounded tool loop. The SDK driver supplies URL checks, input validation, and refusal of disabled tools.

Left out:

- every other tool: screenshots, scrolling, typing, form input, find, tabs, uploads and downloads, console and network reads, JavaScript execution;
- modifier keys on clicks and more than one tab;
- a page that stops answering, including one that opens a dialog (alert, confirm, prompt): every later call, navigate included, fails after 60 seconds;
- request interception. The URL policy judges the address the model asks for, not the browser's own requests. A redirect, or an image or script on an allowed page, can still reach a host the policy would refuse. Block those with egress rules;
- a page that replaces itself by script before its document is parsed: `navigate` then waits for a document that never arrives and gives up after 30 seconds;
- a click, or a page script, that starts a navigation: the next read can fail once or read the old page, and the model reads again or waits;
- cleanup after Ctrl-C: in TypeScript the temporary profile directory is left behind.

A production driver needs most of this list. The SDK guide linked above covers each part.
