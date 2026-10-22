const assert = require("node:assert/strict");
const fs = require("node:fs");
const Module = require("node:module");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

function loadRoute(modelResponse) {
  class MockAnthropic {
    static APIError = class APIError extends Error {};

    messages = {
      create: async () => modelResponse,
    };
  }

  const routePath = path.join(__dirname, "../app/api/finance/route.ts");
  const source = fs.readFileSync(routePath, "utf8");
  const transpiled = ts.transpileModule(source, {
    compilerOptions: {
      esModuleInterop: true,
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
    fileName: routePath,
  }).outputText;

  const routeModule = new Module(routePath, module);
  routeModule.filename = routePath;
  routeModule.paths = Module._nodeModulePaths(path.dirname(routePath));

  const originalLoad = Module._load;
  Module._load = function mockRouteDependencies(request, parent, isMain) {
    if (request === "@anthropic-ai/sdk") {
      return { __esModule: true, default: MockAnthropic };
    }
    if (request === "next/server") {
      return {};
    }
    return originalLoad.call(this, request, parent, isMain);
  };

  try {
    routeModule._compile(transpiled, routePath);
  } finally {
    Module._load = originalLoad;
  }

  return routeModule.exports;
}

test("pie chart conversion preserves a zero-valued metric", async () => {
  const { POST } = loadRoute({
    stop_reason: "tool_use",
    content: [
      {
        type: "tool_use",
        name: "generate_graph_data",
        input: {
          chartType: "pie",
          config: {
            title: "Revenue mix",
            description: "Revenue by product",
            xAxisKey: "category",
          },
          data: [{ category: "Free tier", revenue: 0, value: 99 }],
          chartConfig: {
            revenue: { label: "Revenue" },
          },
        },
      },
    ],
  });

  const response = await POST({
    json: async () => ({
      messages: [{ role: "user", content: "Show revenue by product" }],
      model: "kimi-for-coding",
    }),
  });
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.deepEqual(body.chartData.data, [
    { segment: "Free tier", value: 0 },
  ]);
});
