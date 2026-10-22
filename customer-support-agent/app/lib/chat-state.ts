export type MessageDelivery = "pending" | "complete" | "error";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  delivery?: MessageDelivery;
}

export interface ApiMessage {
  role: ChatMessage["role"];
  content: string;
}

type ChatResponseLike = Pick<Response, "ok" | "status" | "json">;

interface SubmissionLock {
  current: boolean;
}

export class ChatResponseError extends Error {
  constructor(
    public readonly kind: "http" | "parse",
    message: string,
  ) {
    super(message);
    this.name = "ChatResponseError";
  }
}

export function acquireSubmissionLock(lock: SubmissionLock): boolean {
  if (lock.current) return false;
  lock.current = true;
  return true;
}

export function releaseSubmissionLock(lock: SubmissionLock): void {
  lock.current = false;
}

export function replaceMessageById(
  messages: ChatMessage[],
  messageId: string,
  replacement: ChatMessage,
): ChatMessage[] {
  return messages.map((message) =>
    message.id === messageId ? replacement : message,
  );
}

export function buildApiHistory(
  messages: ChatMessage[],
  newUserMessage: ChatMessage,
): ApiMessage[] {
  return [...messages, newUserMessage]
    .filter(
      (message) =>
        message.delivery !== "pending" && message.delivery !== "error",
    )
    .map(({ role, content }) => ({ role, content }));
}

export async function parseChatResponse(
  response: ChatResponseLike,
): Promise<any> {
  if (!response.ok) {
    throw new ChatResponseError(
      "http",
      `API request failed with status ${response.status}`,
    );
  }

  let data: any;
  try {
    data = await response.json();
  } catch {
    throw new ChatResponseError(
      "parse",
      "The support service returned an invalid response",
    );
  }

  if (
    !data ||
    typeof data !== "object" ||
    typeof data.response !== "string" ||
    !data.response.trim()
  ) {
    throw new ChatResponseError(
      "parse",
      "The support service returned an invalid response",
    );
  }

  return data;
}

export function toErrorAssistantMessage(
  pendingMessageId: string,
  error: unknown,
): ChatMessage {
  let response = "Something went wrong. Please try again.";

  if (error instanceof ChatResponseError && error.kind === "http") {
    response = `${error.message}. Please try again.`;
  } else if (error instanceof ChatResponseError && error.kind === "parse") {
    response = `${error.message}. Please try again.`;
  } else if (error instanceof TypeError) {
    response =
      "Could not reach the support service. Check your connection and try again.";
  }

  return {
    id: pendingMessageId,
    role: "assistant",
    delivery: "error",
    content: JSON.stringify({
      response,
      thinking: "The request could not be completed.",
      user_mood: "neutral",
      suggested_questions: [],
      debug: { context_used: false },
    }),
  };
}
