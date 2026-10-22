import Anthropic from '@anthropic-ai/sdk';
import type { BetaAbstractComputerToolset20260801 } from '@anthropic-ai/sdk/helpers/beta/toolsets';
import type { BetaToolResultBlockParam } from '@anthropic-ai/sdk/resources/beta';

const coordinate = {
  type: 'array',
  items: { type: 'integer' },
  minItems: 2,
  maxItems: 2,
  description: '[x, y] screen coordinate',
};
const clickProperties = {
  coordinate,
  text: { type: 'string', description: 'Optional modifier chord such as ctrl or ctrl+shift' },
};

function tool(
  name: string,
  description: string,
  properties: Record<string, unknown>,
  required?: string[],
): Anthropic.Tool {
  return {
    name,
    description,
    input_schema: {
      type: 'object',
      properties,
      additionalProperties: false,
      ...(required ? { required } : {}),
    },
  };
}

export const COMPUTER_TOOLS: Anthropic.Tool[] = [
  tool('screenshot', 'Capture the whole VNC desktop as an image.', {}),
  tool('left_click', 'Left-click at a coordinate or the last pointer position.', clickProperties),
  tool('right_click', 'Right-click at a coordinate or the last pointer position.', clickProperties),
  tool('double_click', 'Double-click at a coordinate or the last pointer position.', clickProperties),
  tool('triple_click', 'Triple-click at a coordinate or the last pointer position.', clickProperties),
  tool('mouse_move', 'Move the pointer to a screen coordinate.', { coordinate }, ['coordinate']),
  tool(
    'scroll',
    'Scroll at a coordinate or the last pointer position.',
    {
      ...clickProperties,
      scroll_direction: { type: 'string', enum: ['up', 'down', 'left', 'right'] },
      scroll_amount: { type: 'integer', minimum: 1, maximum: 10 },
    },
    ['scroll_direction', 'scroll_amount'],
  ),
  tool(
    'key',
    'Press a key, named key, or chord such as Return, Page_Up, or ctrl+s.',
    { text: { type: 'string' }, repeat: { type: 'integer', minimum: 1, maximum: 20 } },
    ['text'],
  ),
  tool('type', 'Type text into the focused application.', { text: { type: 'string' } }, ['text']),
  tool('wait', 'Wait briefly for the desktop to update.', { duration: { type: 'number', minimum: 0, maximum: 30 } }, [
    'duration',
  ]),
];

const MAX_OUTPUT_TOKENS = 8192;

export function dispatch(
  computer: BetaAbstractComputerToolset20260801,
  id: string,
  name: string,
  input: unknown,
): Promise<BetaToolResultBlockParam> {
  return computer.toolResult({ type: 'tool_use', id, name, input, toolset_name: 'computer' });
}

export async function runLoop(
  client: Anthropic,
  computer: BetaAbstractComputerToolset20260801,
  task: string,
  model: string,
  maxTurns = 30,
): Promise<void> {
  const messages: Anthropic.MessageParam[] = [{ role: 'user', content: task }];
  for (let turn = 0; turn < maxTurns; turn++) {
    const response = await client.messages.create({
      model,
      max_tokens: MAX_OUTPUT_TOKENS,
      tools: COMPUTER_TOOLS,
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
      const result = await dispatch(computer, call.id, call.name, call.input);
      const content = (result.content ?? '') as NonNullable<Anthropic.ToolResultBlockParam['content']>;
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
