# Kimi Computer Use Demo

Each user request permits at most 30 model/tool turns. The loop stops with a
visible error when this budget is exhausted, preserving the completed tool
results. Python callers can set `max_tool_turns` to another positive limit.

This demo runs Kimi Code in a Linux desktop container. Kimi can inspect
screenshots, control the mouse and keyboard, run shell commands, and edit files
through ordinary custom tools.

The reference implementation includes:

- A Docker image with a lightweight Linux desktop, Firefox, LibreOffice, and common utilities
- A Kimi Code agent loop using a Messages-compatible API endpoint
- Local computer, shell, and file-editing tools
- A Streamlit chat interface with an embedded noVNC desktop view

> [!CAUTION]
> Computer control carries more risk than an ordinary chat interface. Run this
> demo in a disposable, minimally privileged environment. Do not mount
> sensitive files or credentials. Restrict network access where practical and
> review consequential actions such as purchases, account changes, downloads,
> and acceptance of terms.
>
> Webpages and images can contain prompt-injection text that changes the
> agent's behavior. Inform users of these risks before enabling computer
> control in another product.

> [!IMPORTANT]
> The agent loop runs inside the same container it controls. The demo supports
> one session at a time; restart or reset it between independent sessions when
> necessary.

## Quick start

### Prerequisites

- Docker
- A Kimi Code API key from the [Kimi Code console](https://www.kimi.com/code/console)

### Build and run

From this directory, build the local image:

```bash
docker build -t computer-use-demo:kimi .
```

Set your API key in the shell, then start the container:

```bash
export KIMI_API_KEY=%your_api_key%
docker run --rm -it \
  -e API_PROVIDER=kimi \
  -e KIMI_API_KEY \
  -e KIMI_BASE_URL=https://api.kimi.com/coding/ \
  -p 127.0.0.1:8080:8080 \
  -p 127.0.0.1:8501:8501 \
  -p 127.0.0.1:6080:6080 \
  -p 127.0.0.1:5900:5900 \
  computer-use-demo:kimi
```

Open [http://localhost:8080](http://localhost:8080). The default provider is
Kimi and the default model is `kimi-for-coding`.

For the overseas endpoint, set:

```bash
KIMI_BASE_URL=https://api.kimi.ai/coding/
```

See [Kimi model configuration](https://www.kimi.com/code/docs/en/kimi-code/models)
for current endpoint and account details. Kimi Code is intended for programming
scenarios; use Kimi's open platform for general product integrations.

## How it works

Kimi Code is called through a Messages-compatible endpoint. The Python client
library and some internal environment variable names retain compatibility names,
but the default runtime sends requests to Kimi.

The app presents the computer, bash, and editor capabilities as ordinary custom
tools. When Kimi requests an action, the agent loop runs it inside the container,
captures the result or screenshot, and sends that result back to the model.

```text
User prompt
    │
    ▼
Streamlit UI ──► Kimi Code API
                    │
                    ▼
              custom tool call
                    │
                    ▼
       Linux desktop / shell / editor
                    │
                    ▼
          screenshot or tool result
```

This is a compatibility adaptation using custom tools. It does not depend on a
provider-native computer-control tool type.

## Interfaces

- Combined chat and desktop: [http://localhost:8080](http://localhost:8080)
- Streamlit chat only: [http://localhost:8501](http://localhost:8501)
- noVNC desktop only: [http://localhost:6080/vnc.html](http://localhost:6080/vnc.html)
- Direct VNC connection: `vnc://localhost:5900`

## Screen size

Use the `WIDTH` and `HEIGHT` environment variables to configure the virtual
desktop. The default is 1024x768. For example:

```bash
docker run --rm -it \
  -e API_PROVIDER=kimi \
  -e KIMI_API_KEY \
  -e KIMI_BASE_URL=https://api.kimi.com/coding/ \
  -e WIDTH=1280 \
  -e HEIGHT=800 \
  -p 127.0.0.1:8080:8080 \
  computer-use-demo:kimi
```

Large screenshots increase latency and may reduce coordinate accuracy. The
computer tool scales screenshots and coordinates when the configured desktop
is larger than its model-facing reference size.

## Development

Set up the Python environment and hooks:

```bash
./setup.sh
```

To run local source code inside the container with Streamlit auto-reloading:

```bash
docker build . -t computer-use-demo:local
export KIMI_API_KEY=%your_api_key%
docker run --rm -it \
  -e API_PROVIDER=kimi \
  -e KIMI_API_KEY \
  -e KIMI_BASE_URL=https://api.kimi.com/coding/ \
  -v $(pwd)/computer_use_demo:/home/computeruse/computer_use_demo/ \
  -p 127.0.0.1:5900:5900 \
  -p 127.0.0.1:8501:8501 \
  -p 127.0.0.1:6080:6080 \
  -p 127.0.0.1:8080:8080 \
  computer-use-demo:local
```

## Troubleshooting

If authentication fails, verify that `KIMI_API_KEY` is present in the shell
that launches Docker. If requests target the wrong endpoint, check
`KIMI_BASE_URL` and restart the container.

If the desktop is blank, open the noVNC URL directly and confirm that ports
6080 and 8080 are available. If actions land at the wrong coordinates, return
to the default 1024x768 screen size before testing custom dimensions.
