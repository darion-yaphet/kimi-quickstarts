const assert = require("node:assert/strict");
const fs = require("node:fs");
const Module = require("node:module");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

function loadChatState() {
  const modulePath = path.join(__dirname, "../app/lib/chat-state.ts");
  const source = fs.readFileSync(modulePath, "utf8");
  const transpiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
    fileName: modulePath,
  }).outputText;

  const chatStateModule = new Module(modulePath, module);
  chatStateModule.filename = modulePath;
  chatStateModule.paths = Module._nodeModulePaths(path.dirname(modulePath));
  chatStateModule._compile(transpiled, modulePath);
  return chatStateModule.exports;
}

test("a response replaces its own pending message even when it is not last", () => {
  const { replaceMessageById } = loadChatState();
  const messages = [
    { id: "user-1", role: "user", content: "hello" },
    {
      id: "pending-1",
      role: "assistant",
      content: "pending",
      delivery: "pending",
    },
    { id: "later", role: "user", content: "later message" },
  ];

  const updated = replaceMessageById(messages, "pending-1", {
    id: "pending-1",
    role: "assistant",
    content: "finished",
    delivery: "complete",
  });

  assert.deepEqual(updated, [
    messages[0],
    {
      id: "pending-1",
      role: "assistant",
      content: "finished",
      delivery: "complete",
    },
    messages[2],
  ]);
});

test("API history excludes pending and failed assistant messages", () => {
  const { buildApiHistory } = loadChatState();
  const history = buildApiHistory(
    [
      { id: "user-1", role: "user", content: "first question" },
      {
        id: "error-1",
        role: "assistant",
        content: "visible error",
        delivery: "error",
      },
      {
        id: "pending-1",
        role: "assistant",
        content: "pending",
        delivery: "pending",
      },
      {
        id: "answer-1",
        role: "assistant",
        content: "first answer",
        delivery: "complete",
      },
    ],
    { id: "user-2", role: "user", content: "second question" },
  );

  assert.deepEqual(history, [
    { role: "user", content: "first question" },
    { role: "assistant", content: "first answer" },
    { role: "user", content: "second question" },
  ]);
});

test("a submission lock rejects a second click until the request finishes", () => {
  const { acquireSubmissionLock, releaseSubmissionLock } = loadChatState();
  const lock = { current: false };

  assert.equal(acquireSubmissionLock(lock), true);
  assert.equal(acquireSubmissionLock(lock), false);

  releaseSubmissionLock(lock);
  assert.equal(acquireSubmissionLock(lock), true);
});

test("HTTP and JSON failures become immediate visible error messages", async (t) => {
  const { parseChatResponse, toErrorAssistantMessage } = loadChatState();

  async function visibleErrorFor(response) {
    try {
      await parseChatResponse(response);
      assert.fail("expected the response to fail");
    } catch (error) {
      const message = toErrorAssistantMessage("pending-1", error);
      return JSON.parse(message.content).response;
    }
  }

  await t.test("HTTP status", async () => {
    const visibleError = await visibleErrorFor({
      ok: false,
      status: 503,
      json: async () => ({}),
    });

    assert.match(visibleError, /status 503/);
  });

  await t.test("invalid JSON", async () => {
    const visibleError = await visibleErrorFor({
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError("Unexpected token");
      },
    });

    assert.match(visibleError, /invalid response/i);
  });

  await t.test("network failure", () => {
    const message = toErrorAssistantMessage(
      "pending-1",
      new TypeError("Failed to fetch"),
    );
    const content = JSON.parse(message.content);

    assert.equal(message.id, "pending-1");
    assert.equal(message.delivery, "error");
    assert.match(content.response, /reach the support service/i);
  });
});

test("a successful response requires non-empty response text", async (t) => {
  const { parseChatResponse } = loadChatState();

  await t.test("accepts valid response JSON", async () => {
    const body = { response: "Here is the answer", thinking: "Found it" };
    const parsed = await parseChatResponse({
      ok: true,
      status: 200,
      json: async () => body,
    });

    assert.deepEqual(parsed, body);
  });

  for (const [name, body] of [
    ["missing response", { thinking: "No answer" }],
    ["empty response", { response: "   " }],
    ["non-string response", { response: 42 }],
  ]) {
    await t.test(name, async () => {
      await assert.rejects(
        parseChatResponse({
          ok: true,
          status: 200,
          json: async () => body,
        }),
        /invalid response/i,
      );
    });
  }
});
