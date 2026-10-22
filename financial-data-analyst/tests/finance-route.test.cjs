const assert = require("node:assert/strict");
const fs = require("node:fs");
const Module = require("node:module");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

function loadRoute(modelResponse, onModelRequest = () => {}) {
  class MockAnthropic {
    static APIError = class APIError extends Error {};
    static AuthenticationError = class AuthenticationError extends Error {};

    messages = {
      create: async (request) => {
        onModelRequest(request);
        return modelResponse;
      },
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

test("a follow-up request includes the previous chart structure", async () => {
  const previousChart = validChart("bar");
  let modelRequest;
  const { POST } = loadRoute(
    {
      stop_reason: "end_turn",
      content: [{ type: "text", text: "Updated the chart." }],
    },
    (request) => {
      modelRequest = request;
    },
  );

  const response = await POST({
    json: async () => ({
      messages: [
        { role: "user", content: "Create a revenue chart" },
        {
          role: "assistant",
          content: "",
          chartData: previousChart,
        },
        { role: "user", content: "Modify the previous chart" },
      ],
      model: "kimi-for-coding",
    }),
  });

  assert.equal(response.status, 200);
  assert.equal(modelRequest.messages[1].role, "assistant");
  assert.ok(Array.isArray(modelRequest.messages[1].content));
  const chartContext = modelRequest.messages[1].content.find(
    (block) => block.type === "text" && block.text.includes("chartData"),
  );
  assert.ok(chartContext);
  assert.match(chartContext.text, /"data":\[/);
  assert.match(chartContext.text, /"config":\{/);
  assert.match(chartContext.text, /"chartConfig":\{/);
  assert.match(chartContext.text, /"revenue":125/);
});

test("plain text and image history are passed to the model unchanged", async () => {
  const imageContent = [
    {
      type: "image",
      source: {
        type: "base64",
        media_type: "image/png",
        data: "aW1hZ2U=",
      },
    },
    { type: "text", text: "Analyze this image" },
  ];
  const messages = [
    { role: "user", content: "Plain text question" },
    { role: "assistant", content: "Plain text answer" },
    { role: "user", content: imageContent },
  ];
  let modelRequest;
  const { POST } = loadRoute(
    {
      stop_reason: "end_turn",
      content: [{ type: "text", text: "Image analyzed." }],
    },
    (request) => {
      modelRequest = request;
    },
  );

  const response = await POST({
    json: async () => ({ messages, model: "kimi-for-coding" }),
  });

  assert.equal(response.status, 200);
  assert.deepEqual(modelRequest.messages, messages);
});

async function postChart(chartInput) {
  const { POST } = loadRoute({
    stop_reason: "tool_use",
    content: [
      {
        type: "tool_use",
        name: "generate_graph_data",
        input: chartInput,
      },
    ],
  });

  const response = await POST({
    json: async () => ({
      messages: [{ role: "user", content: "Show the data" }],
      model: "kimi-for-coding",
    }),
  });

  return { response, body: await response.json() };
}

function validChart(chartType) {
  const chartConfig =
    chartType === "multiBar" || chartType === "stackedArea"
      ? {
          revenue: { label: "Revenue" },
          costs: { label: "Costs", stacked: chartType === "stackedArea" },
        }
      : { revenue: { label: "Revenue" } };

  return {
    chartType,
    config: {
      title: "Financial performance",
      description: "Performance by period",
      xAxisKey: chartType === "pie" ? "category" : "period",
      trend: { percentage: 10, direction: "up" },
    },
    data:
      chartType === "pie"
        ? [{ category: "Subscriptions", revenue: 125 }]
        : [{ period: "Q1 2025", revenue: 125, costs: 75 }],
    chartConfig,
  };
}

test("pie chart conversion preserves zero and supports an omitted xAxisKey", async () => {
  const { response, body } = await postChart({
    chartType: "pie",
    config: {
      title: "Revenue mix",
      description: "Revenue by product",
    },
    data: [{ category: "Free tier", revenue: 0, value: 99 }],
    chartConfig: {
      revenue: { label: "Revenue" },
    },
  });

  assert.equal(response.status, 200);
  assert.deepEqual(body.chartData.data, [
    { segment: "Free tier", value: 0 },
  ]);
});

test("chart validation reports a missing config object clearly", async () => {
  const chart = validChart("bar");
  delete chart.config;

  const { response, body } = await postChart(chart);

  assert.equal(response.status, 422);
  assert.equal(body.error, "Invalid chart data");
  assert.match(body.details, /config/i);
});

test("chart validation rejects non-finite numeric series values", async () => {
  const chart = validChart("line");
  chart.data[0].revenue = Number.POSITIVE_INFINITY;

  const { response, body } = await postChart(chart);

  assert.equal(response.status, 422);
  assert.equal(body.error, "Invalid chart data");
  assert.match(body.details, /finite number/i);
});

test("non-pie charts require string x-axis labels", async () => {
  const chart = validChart("bar");
  chart.data[0].period = 2025;

  const { response, body } = await postChart(chart);

  assert.equal(response.status, 422);
  assert.equal(body.error, "Invalid chart data");
  assert.match(body.details, /period must be a non-empty string/i);
});

test("chart validation accepts all supported chart types", async (t) => {
  for (const chartType of [
    "bar",
    "multiBar",
    "line",
    "pie",
    "area",
    "stackedArea",
  ]) {
    await t.test(chartType, async () => {
      const { response, body } = await postChart(validChart(chartType));

      assert.equal(response.status, 200);
      assert.equal(body.chartData.chartType, chartType);
      assert.equal(body.chartData.config.title, "Financial performance");
      assert.equal(body.chartData.data.length, 1);
    });
  }
});
