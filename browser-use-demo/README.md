# Browser Automation Demo with Kimi Code

A containerized Playwright browser agent using Kimi Code through its
Anthropic-compatible Messages API. Browser actions remain ordinary custom
tools and screenshots remain standard image content blocks.

> [!TIP]
> This is a deliberately minimal, containerized reference focused on browser-only DOM automation. For a full GUI agent that can also operate desktop applications, see the [computer use demo](../computer-use-demo).

## Overview

This demo implements a custom browser tool that enables Kimi Code to interact with web browsers. It provides:

- **DOM access**: Read page structure with element references
- **Navigation control**: Browse URLs and manage browser history
- **Form manipulation**: Directly set form input values
- **Text extraction**: Get all text content from pages
- **Element targeting**: Interact with elements via ref or coordinate parameters
- **Smart scrolling**: Scroll to specific elements or in specific directions
- **Page search**: Find and highlight text on pages
- **Visual capture**: Take screenshots and capture zoomed regions

### Advantages Over Coordinate-Based Automation

- **Reliability**: Element-based targeting via the `ref` parameter works across different screen sizes and layouts, unlike pixel coordinates that break when windows resize
- **Direct DOM manipulation**: Provides structured visibility into page elements and their properties, enabling precise interactions with dynamic content, hidden elements, and complex web applications
- **Web-specific actions**: Built-in support for navigation, text extraction, and form completion

## Quick Start

### Prerequisites

- Docker and Docker Compose installed on your system
- Kimi Code API key

### Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/darion-yaphet/kimi-quickstarts.git
   cd kimi-quickstarts/browser-use-demo
   ```

2. **Configure environment**:
   ```bash
   cp .env.example .env
   # Add your Kimi Code key as KIMI_API_KEY.
   # ANTHROPIC_BASE_URL and KIMI_MODEL already have working defaults.
   ```

   The display resolution is set to 1920x1080 (16:9) for optimal coordinate accuracy.
   - See `.env.example` for more options and coordinate scaling details

   `KIMI_API_KEY` takes precedence over the Anthropic-compatible fallback
   `ANTHROPIC_API_KEY`. If you use the optional local key file, Kimi reads only
   `~/.kimi/api_key`; it never reuses `~/.anthropic/api_key`. Switching the
   provider in the UI replaces the credential and clears conversation history.
   Manage Kimi Code keys at https://www.kimi.com/code/console.

### Running with Docker Compose

```bash
# For production use:
docker-compose up --build

# For development with file watching (auto-sync changes):
docker-compose up --build --watch
```

### Access the interfaces:
   - **Main UI**: http://localhost:8080 (Streamlit interface)
   - **NoVNC Browser View**: http://localhost:6080 (see the browser)
   - **VNC**: Connect any VNC client to localhost:5900

https://github.com/user-attachments/assets/4fb72078-6902-4b63-bcd1-5f2c4cd60582

## Usage Examples

Once the demo is running, try these prompts in the Streamlit interface:

- "Navigate to news.ycombinator.com and tell me the top 3 stories"
- "Go to google.com and search for 'Kimi Code'"
- "Visit wikipedia.org and find information about artificial intelligence"
- "Navigate to github.com and search for 'playwright'"

The current Playwright implementation may encounter CAPTCHAs on search engines. When that happens, ask the agent to navigate directly to a specific website and search within that site.

## Safety Considerations

Browser automation poses unique risks that are distinct from standard API features or chat interfaces. These risks are heightened when using the tool to interact with the internet. To minimize risks, consider taking precautions such as:

1. Run the browser in an isolated virtual machine or container environment with minimal privileges to prevent direct system attacks or accidents.
2. Avoid giving the model access to sensitive data, such as account login information, to prevent information theft.
3. Consider an allowlist of domains to reduce exposure to malicious content.
4. Ask a human to confirm decisions that may result in meaningful real-world consequences as well as any tasks requiring affirmative consent, such as accepting cookies, executing financial transactions, or agreeing to terms of service.

In some circumstances, the model may follow commands found in page content even when they conflict with the user's instructions. Webpages or images can contain prompt-injection text that changes the agent's behavior. Isolate the agent from sensitive data and actions, and review consequential operations.

Finally, please inform end users of relevant risks and obtain their consent prior to enabling browser automation in your own products.

This demo runs a browser in a containerized environment. While isolated, please note:

- **Don't enter personal credentials or sensitive information** - This is a demonstration tool
- **Be cautious about the websites you visit** - Some sites may have anti-automation measures
- **Not for production use** - This demo is for learning and development purposes only

## Implementation Reference

This demo shows how to build browser automation with Kimi Code and Playwright. All browser actions (navigate, click, type, scroll, form_input, etc.) are implemented as methods in [browser.py](browser_use_demo/tools/browser.py) using Playwright's async API.

### Key Files

- **[browser.py](browser_use_demo/tools/browser.py)** - Main tool with all browser actions
- **[loop.py](browser_use_demo/loop.py)** - Sampling loop for API calls and response handling
- **[streamlit.py](browser_use_demo/streamlit.py)** - Chat UI
- **[browser_tool_utils/](browser_use_demo/browser_tool_utils/)** - JavaScript utilities for DOM extraction, element finding, and form manipulation

### Core Patterns

**Element references:** JavaScript utilities generate `ref` identifiers for reliable element targeting across screen sizes (replacing brittle pixel coordinates).

**Tool setup:**
```python
browser_tool = BrowserTool()

def to_params(self):
    return {
        "name": "browser",
        "description": BROWSER_TOOL_DESCRIPTION,
        "input_schema": BROWSER_TOOL_INPUT_SCHEMA,
    }
```

### Coordinate Scaling

The browser tools implementation includes automatic coordinate scaling to ensure accurate interactions:

**How it works:**
- The browser viewport is fixed at 1920x1080 (16:9 aspect ratio)
- The implementation uses a 1456x819 model-facing reference resolution for 16:9 screenshots
- The browser tool automatically scales coordinates from that reference resolution to the actual viewport (1920x1080)
- This ensures clicks and interactions happen at the correct locations

See `browser_use_demo/tools/coordinate_scaling.py` for the implementation.

This demo uses a custom tool definition with an explicit input schema, giving you full control over the tool interface. The `BROWSER_TOOL_DESCRIPTION` and `BROWSER_TOOL_INPUT_SCHEMA` constants in [browser.py](browser_use_demo/tools/browser.py) provide a complete example you can use as a starting point for your own browser automation tools.


### Modifying & Using as a Template

**To modify this demo:**
1. Edit `browser_use_demo/tools/browser.py` to add features or change behavior
2. Rebuild the Docker image (volume mount allows live Python code updates)

**To use as a template for your own project:**
1. Copy [browser.py](browser_use_demo/tools/browser.py) and [browser_tool_utils/](browser_use_demo/browser_tool_utils/)
2. Adapt [loop.py](browser_use_demo/loop.py) for your API integration
3. Build your UI or use [streamlit.py](browser_use_demo/streamlit.py) as a starting point

## Architecture

```
┌──────────────────────────────────┐
│     Docker Container              │
│                                   │
│  ┌─────────────────────────────┐ │
│  │   Streamlit Interface       │ │  ← User interacts here
│  └──────────┬──────────────────┘ │
│             │                     │
│  ┌──────────▼──────────────────┐ │
│  │   Kimi API + Browser Tool   │ │  ← Kimi controls browser
│  └──────────┬──────────────────┘ │
│             │                     │
│  ┌──────────▼──────────────────┐ │
│  │   Playwright + Chromium     │ │  ← Browser automation
│  └──────────┬──────────────────┘ │
│             │                     │
│  ┌──────────▼──────────────────┐ │
│  │   XVFB Virtual Display      │ │  ← Virtual display
│  └──────────┬──────────────────┘ │
│             │                     │
│  ┌──────────▼──────────────────┐ │
│  │   VNC/NoVNC Server          │ │  ← Visual access
│  └─────────────────────────────┘ │
└──────────────────────────────────┘
```

## How Browser Automation Differs from Computer Use

This browser automation demo is specifically optimized for web automation with DOM-aware features like element targeting, page reading, and form manipulation. While it shares many capabilities with the [computer use demo](../computer-use-demo), browser automation adds web-specific actions and the ability to target elements by reference instead of just coordinates. Computer use provides general desktop control for any application, while browser automation focuses exclusively on browser-based tasks.

### Actions Unique to Browser Automation

These web-specific actions are not available in computer use:

- **navigate**: Navigate to URL or use "back"/"forward" for history (requires text)
- **read_page**: Get DOM tree with element refs; use text="interactive" to filter
- **get_page_text**: Extract all text content from the page
- **find**: Search for text and highlight matches (requires text)
- **form_input**: Set form element value directly (requires ref and value)
- **scroll_to**: Scroll element into view (requires ref)
- **execute_js**: Run JavaScript code in page context (requires text with JS code)

### Actions Shared with Computer Use

These actions work similarly to their computer use counterparts. The key difference is that browser automation allows targeting by element reference (`ref`) as an alternative to coordinates:

**Mouse Actions** (accept either `ref` or `coordinate`):
- **left_click**, **right_click**, **middle_click**, **double_click**, **triple_click**
- **hover**: Move cursor without clicking (for tooltips, dropdowns)
- **left_click_drag**: Drag from start_coordinate to coordinate
- **left_mouse_down**, **left_mouse_up**: Fine-grained mouse control

**Keyboard Actions**:
- **type**: Type text at cursor (requires text)
- **key**: Press key or combination (requires text)
- **hold_key**: Hold key for duration (requires text and duration)

**Other**:
- **screenshot**: Capture current viewport
- **scroll**: Scroll in direction (requires scroll_direction, scroll_amount, coordinate)
- **zoom**: Zoomed screenshot of region (requires region: x1, y1, x2, y2)
- **wait**: Wait for duration in seconds (requires duration, 0-100)

### Computer Use Actions Not Included

These desktop-level actions from computer use are not in this browser demo:

- **cursor_position**: Get current (x, y) pixel coordinate of cursor

This is less relevant for browser automation since the `ref` parameter provides reliable element-based targeting, replacing the need for cursor tracking. Note that `hover` provides similar functionality to `mouse_move` for triggering hover states.


## Troubleshooting

**Browser not visible?**
- Check that port 6080 is accessible
- Try refreshing the NoVNC page
- Ensure Docker has sufficient resources allocated

**API errors?**
- Verify your Kimi Code key is set as `KIMI_API_KEY`
- Verify `ANTHROPIC_BASE_URL` and `KIMI_MODEL` match your Kimi Code account

**Browser actions failing?**
- Some websites may have anti-automation measures
- Try simpler websites first to test functionality
- Check the browser view to see what's happening

## Attribution

This software includes components from Microsoft Playwright. See the [NOTICE](NOTICE) file for details.

## Credits

Built with:
- [Kimi Code API](https://www.kimi.com/code/docs/)
- [Playwright](https://playwright.dev)
- [Streamlit](https://streamlit.io)
- [NoVNC](https://novnc.com)
