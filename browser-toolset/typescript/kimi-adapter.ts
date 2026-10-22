import Anthropic from '@anthropic-ai/sdk';
import type { BetaAbstractBrowserToolset20260801 } from '@anthropic-ai/sdk/helpers/beta/toolsets';
import type { BetaToolResultBlockParam } from '@anthropic-ai/sdk/resources/beta';

export const BROWSER_TOOLS: Anthropic.Tool[] = [
  {
    name: 'navigate',
    description: 'Navigate the active browser tab to an HTTP(S) URL allowed by the local URL policy.',
    input_schema: {
      type: 'object',
      properties: { url: { type: 'string', description: 'URL to open' } },
      required: ['url'],
      additionalProperties: false,
    },
  },
  {
    name: 'read_page',
    description: 'List visible headings, links, buttons and form controls with refs for clicking.',
    input_schema: { type: 'object', properties: {}, additionalProperties: false },
  },
  {
    name: 'left_click',
    description: 'Left-click a current element ref from read_page, or a viewport coordinate.',
    input_schema: {
      type: 'object',
      properties: {
        target: {
          oneOf: [
            {
              type: 'object',
              properties: { type: { const: 'ref' }, ref: { type: 'string' } },
              required: ['type', 'ref'],
              additionalProperties: false,
            },
            {
              type: 'object',
              properties: {
                type: { const: 'coordinate' },
                x: { type: 'number' },
                y: { type: 'number' },
              },
              required: ['type', 'x', 'y'],
              additionalProperties: false,
            },
          ],
        },
      },
      required: ['target'],
      additionalProperties: false,
    },
  },
  {
    name: 'get_page_text',
    description: 'Return the visible text of the current page.',
    input_schema: { type: 'object', properties: {}, additionalProperties: false },
  },
  {
    name: 'wait',
    description: 'Wait up to 30 seconds for the page to change.',
    input_schema: {
      type: 'object',
      properties: { duration: { type: 'number', minimum: 0, maximum: 30 } },
      required: ['duration'],
      additionalProperties: false,
    },
  },
];

const MAX_OUTPUT_TOKENS = 8192;

export function portableContent(
  result: BetaToolResultBlockParam,
): NonNullable<Anthropic.ToolResultBlockParam['content']> {
  if (typeof result.content === 'string' || result.content === undefined) return result.content ?? '';
  return result.content.map((block) => {
    if (block.type !== 'browser_state') return block;
    const { type: _type, ...state } = block;
    return { type: 'text', text: `Browser state: ${JSON.stringify(state)}` };
  }) as NonNullable<Anthropic.ToolResultBlockParam['content']>;
}

export function dispatch(
  browser: BetaAbstractBrowserToolset20260801,
  id: string,
  name: string,
  input: unknown,
): Promise<BetaToolResultBlockParam> {
  return browser.toolResult({ type: 'tool_use', id, name, input, toolset_name: 'browser' });
}

export async function runLoop(
  client: Anthropic,
  browser: BetaAbstractBrowserToolset20260801,
  task: string,
  model: string,
  maxTurns = 20,
): Promise<void> {
  const messages: Anthropic.MessageParam[] = [{ role: 'user', content: task }];
  for (let turn = 0; turn < maxTurns; turn++) {
    const response = await client.messages.create({
      model,
      max_tokens: MAX_OUTPUT_TOKENS,
      tools: BROWSER_TOOLS,
      messages,
    });
    console.dir(response, { depth: 4 });
    if (response.stop_reason === 'max_tokens') {
      throw new Error(
        `model response was truncated at ${MAX_OUTPUT_TOKENS} output tokens before the tool turn completed`,
      );
    }
    messages.push({ role: 'assistant', content: response.content });
    const calls = response.content.filter((block): block is Anthropic.ToolUseBlock => block.type === 'tool_use');
    if (calls.length === 0) return;
    const results: Anthropic.ToolResultBlockParam[] = [];
    for (const call of calls) {
      const result = await dispatch(browser, call.id, call.name, call.input);
      const content = portableContent(result);
      results.push({
        type: 'tool_result',
        tool_use_id: call.id,
        content,
        ...(result.is_error ? { is_error: true } : {}),
      });
    }
    messages.push({ role: 'user', content: results });
  }
  throw new Error(`model did not finish within ${maxTurns} tool turns`);
}
