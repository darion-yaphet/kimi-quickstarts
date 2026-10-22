// app/api/finance/route.ts
import { NextRequest } from "next/server";
import Anthropic from "@anthropic-ai/sdk";
import type { ChartData } from "@/types/chart";

// Kimi Code exposes an Anthropic-compatible Messages endpoint.
const anthropic = new Anthropic({
  apiKey: process.env.ANTHROPIC_API_KEY!,
  baseURL:
    process.env.ANTHROPIC_BASE_URL || "https://api.kimi.com/coding/",
});

export const runtime = "edge";

// Helper to validate base64
const isValidBase64 = (str: string) => {
  try {
    return btoa(atob(str)) === str;
  } catch (err) {
    return false;
  }
};

// Add Type Definitions
interface ChartToolResponse extends ChartData {
  // Any additional properties specific to the tool response
}

const supportedChartTypes = new Set<ChartData["chartType"]>([
  "bar",
  "multiBar",
  "line",
  "pie",
  "area",
  "stackedArea",
]);

class ChartValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ChartValidationError";
  }
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const requireRecord = (
  value: unknown,
  path: string,
): Record<string, unknown> => {
  if (!isRecord(value)) {
    throw new ChartValidationError(`${path} must be an object`);
  }
  return value;
};

const requireNonEmptyString = (value: unknown, path: string): string => {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new ChartValidationError(`${path} must be a non-empty string`);
  }
  return value;
};

const requireFiniteNumber = (value: unknown, path: string): number => {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new ChartValidationError(`${path} must be a finite number`);
  }
  return value;
};

const getPieSegment = (
  row: Record<string, unknown>,
  xAxisKey?: string,
): unknown =>
  row[xAxisKey || "segment"] || row.segment || row.category || row.name;

const validateChartData = (input: unknown): ChartToolResponse => {
  const chartData = requireRecord(input, "chartData");

  if (!supportedChartTypes.has(chartData.chartType as ChartData["chartType"])) {
    throw new ChartValidationError("chartData.chartType is not supported");
  }

  const config = requireRecord(chartData.config, "chartData.config");
  requireNonEmptyString(config.title, "chartData.config.title");
  requireNonEmptyString(
    config.description,
    "chartData.config.description",
  );
  const xAxisKey =
    chartData.chartType === "pie" && config.xAxisKey === undefined
      ? undefined
      : requireNonEmptyString(
          config.xAxisKey,
          "chartData.config.xAxisKey",
        );

  for (const key of ["footer", "totalLabel"] as const) {
    if (config[key] !== undefined && typeof config[key] !== "string") {
      throw new ChartValidationError(
        `chartData.config.${key} must be a string`,
      );
    }
  }

  if (config.trend !== undefined) {
    const trend = requireRecord(config.trend, "chartData.config.trend");
    requireFiniteNumber(
      trend.percentage,
      "chartData.config.trend.percentage",
    );
    if (trend.direction !== "up" && trend.direction !== "down") {
      throw new ChartValidationError(
        'chartData.config.trend.direction must be "up" or "down"',
      );
    }
  }

  const chartConfig = requireRecord(
    chartData.chartConfig,
    "chartData.chartConfig",
  );
  const seriesKeys = Object.keys(chartConfig);
  if (seriesKeys.length === 0) {
    throw new ChartValidationError(
      "chartData.chartConfig must define at least one series",
    );
  }

  for (const seriesKey of seriesKeys) {
    requireNonEmptyString(seriesKey, "chartData.chartConfig series key");
    const series = requireRecord(
      chartConfig[seriesKey],
      `chartData.chartConfig.${seriesKey}`,
    );
    requireNonEmptyString(
      series.label,
      `chartData.chartConfig.${seriesKey}.label`,
    );
    if (series.stacked !== undefined && typeof series.stacked !== "boolean") {
      throw new ChartValidationError(
        `chartData.chartConfig.${seriesKey}.stacked must be a boolean`,
      );
    }
  }

  if (!Array.isArray(chartData.data) || chartData.data.length === 0) {
    throw new ChartValidationError(
      "chartData.data must be a non-empty array",
    );
  }

  chartData.data.forEach((rowValue, rowIndex) => {
    const row = requireRecord(rowValue, `chartData.data[${rowIndex}]`);

    if (chartData.chartType === "pie") {
      requireNonEmptyString(
        getPieSegment(row, xAxisKey),
        `chartData.data[${rowIndex}] pie segment`,
      );
      const valueKey = seriesKeys[0];
      requireFiniteNumber(
        row[valueKey] ?? row.value,
        `chartData.data[${rowIndex}].${valueKey}`,
      );
      return;
    }

    requireNonEmptyString(
      row[xAxisKey!],
      `chartData.data[${rowIndex}].${xAxisKey}`,
    );

    for (const seriesKey of seriesKeys) {
      requireFiniteNumber(
        row[seriesKey],
        `chartData.data[${rowIndex}].${seriesKey}`,
      );
    }
  });

  return chartData as unknown as ChartToolResponse;
};

interface ToolSchema {
  name: string;
  description: string;
  input_schema: {
    type: "object";
    properties: Record<string, unknown>;
    required: string[];
  };
}

const tools: ToolSchema[] = [
  {
    name: "generate_graph_data",
    description:
      "Generate structured JSON data for creating financial charts and graphs.",
    input_schema: {
      type: "object" as const,
      properties: {
        chartType: {
          type: "string" as const,
          enum: [
            "bar",
            "multiBar",
            "line",
            "pie",
            "area",
            "stackedArea",
          ] as const,
          description: "The type of chart to generate",
        },
        config: {
          type: "object" as const,
          description:
            "Chart presentation settings. xAxisKey is required for every non-pie chart and its row values must be strings; pie charts may omit it and use segment, category, or name labels.",
          properties: {
            title: { type: "string" as const },
            description: { type: "string" as const },
            trend: {
              type: "object" as const,
              properties: {
                percentage: { type: "number" as const },
                direction: {
                  type: "string" as const,
                  enum: ["up", "down"] as const,
                },
              },
              required: ["percentage", "direction"],
            },
            footer: { type: "string" as const },
            totalLabel: { type: "string" as const },
            xAxisKey: {
              type: "string" as const,
              description:
                "Required for non-pie charts; identifies a string label in every data row. Optional for pie charts.",
            },
          },
          required: ["title", "description"],
        },
        data: {
          type: "array" as const,
          description:
            "Chart rows. Every configured series value must be a finite number. Non-pie x-axis values must be strings.",
          items: {
            type: "object" as const,
            additionalProperties: true, // Allow any structure
          },
        },
        chartConfig: {
          type: "object" as const,
          additionalProperties: {
            type: "object" as const,
            properties: {
              label: { type: "string" as const },
              stacked: { type: "boolean" as const },
            },
            required: ["label"],
          },
        },
      },
      required: ["chartType", "config", "data", "chartConfig"],
    },
  },
];

const serializeMessageForModel = (message: {
  role: Anthropic.MessageParam["role"];
  content: Anthropic.MessageParam["content"];
  chartData?: unknown;
}): Anthropic.MessageParam => {
  if (message.role !== "assistant" || message.chartData === undefined) {
    return { role: message.role, content: message.content };
  }

  const content: Exclude<Anthropic.MessageParam["content"], string> = Array.isArray(
    message.content,
  )
    ? [...message.content]
    : typeof message.content === "string" && message.content.length > 0
      ? [{ type: "text" as const, text: message.content }]
      : [];

  content.push({
    type: "text" as const,
    text: `Previously generated chartData (use this structure when the user asks to modify the chart):\n${JSON.stringify(message.chartData)}`,
  });

  return { role: message.role, content };
};

export async function POST(req: NextRequest) {
  try {
    const { messages, fileData, model } = await req.json();

    console.log("🔍 Initial Request Data:", {
      hasMessages: !!messages,
      messageCount: messages?.length,
      hasFileData: !!fileData,
      fileType: fileData?.mediaType,
      model,
    });

    // Input validation
    if (!messages || !Array.isArray(messages)) {
      return new Response(
        JSON.stringify({ error: "Messages array is required" }),
        { status: 400 },
      );
    }

    if (!model) {
      return new Response(
        JSON.stringify({ error: "Model selection is required" }),
        { status: 400 },
      );
    }

    // Convert all previous messages
    let anthropicMessages = messages.map(serializeMessageForModel);

    // Handle file in the latest message
    if (fileData) {
      const { base64, mediaType, isText } = fileData;

      if (!base64) {
        console.error("❌ No base64 data received");
        return new Response(JSON.stringify({ error: "No file data" }), {
          status: 400,
        });
      }

      try {
        if (isText) {
          // Decode base64 text content
          const textContent = decodeURIComponent(escape(atob(base64)));

          // Replace only the last message with the file content
          anthropicMessages[anthropicMessages.length - 1] = {
            role: "user",
            content: [
              {
                type: "text",
                text: `File contents of ${fileData.fileName}:\n\n${textContent}`,
              },
              {
                type: "text",
                text: messages[messages.length - 1].content,
              },
            ],
          };
        } else if (mediaType.startsWith("image/")) {
          // Handle image files
          anthropicMessages[anthropicMessages.length - 1] = {
            role: "user",
            content: [
              {
                type: "image",
                source: {
                  type: "base64",
                  media_type: mediaType,
                  data: base64,
                },
              },
              {
                type: "text",
                text: messages[messages.length - 1].content,
              },
            ],
          };
        }
      } catch (error) {
        console.error("Error processing file content:", error);
        return new Response(
          JSON.stringify({ error: "Failed to process file content" }),
          { status: 400 },
        );
      }
    }

    console.log("🚀 Final model API request:", {
      endpoint: "messages.create",
      model,
      max_tokens: 4096,
      messageCount: anthropicMessages.length,
      tools: tools.map((t) => t.name),
      messageStructure: JSON.stringify(
        anthropicMessages.map((msg) => ({
          role: msg.role,
          content:
            typeof msg.content === "string"
              ? msg.content.slice(0, 50) + "..."
              : "[Complex Content]",
        })),
        null,
        2,
      ),
    });

    const response = await anthropic.messages.create({
      model,
      max_tokens: 4096,
      tools: tools,
      tool_choice: { type: "auto" },
      messages: anthropicMessages,
      system: `You are a financial data visualization expert. Your role is to analyze financial data and create clear, meaningful visualizations using generate_graph_data tool:

Here are the chart types available and their ideal use cases:

1. LINE CHARTS ("line")
   - Time series data showing trends
   - Financial metrics over time
   - Market performance tracking

2. BAR CHARTS ("bar")
   - Single metric comparisons
   - Period-over-period analysis
   - Category performance

3. MULTI-BAR CHARTS ("multiBar")
   - Multiple metrics comparison
   - Side-by-side performance analysis
   - Cross-category insights

4. AREA CHARTS ("area")
   - Volume or quantity over time
   - Cumulative trends
   - Market size evolution

5. STACKED AREA CHARTS ("stackedArea")
   - Component breakdowns over time
   - Portfolio composition changes
   - Market share evolution

6. PIE CHARTS ("pie")
   - Distribution analysis
   - Market share breakdown
   - Portfolio allocation

When generating visualizations:
1. Structure data correctly based on the chart type
2. Use descriptive titles and clear descriptions
3. Include trend information when relevant (percentage and direction)
4. Add contextual footer notes
5. Use proper data keys that reflect the actual metrics

Data Structure Examples:

For Time-Series (Line/Bar/Area):
{
  data: [
    { period: "Q1 2024", revenue: 1250000 },
    { period: "Q2 2024", revenue: 1450000 }
  ],
  config: {
    xAxisKey: "period",
    title: "Quarterly Revenue",
    description: "Revenue growth over time"
  },
  chartConfig: {
    revenue: { label: "Revenue ($)" }
  }
}

For Comparisons (MultiBar):
{
  data: [
    { category: "Product A", sales: 450000, costs: 280000 },
    { category: "Product B", sales: 650000, costs: 420000 }
  ],
  config: {
    xAxisKey: "category",
    title: "Product Performance",
    description: "Sales vs Costs by Product"
  },
  chartConfig: {
    sales: { label: "Sales ($)" },
    costs: { label: "Costs ($)" }
  }
}

For Distributions (Pie):
{
  data: [
    { segment: "Equities", value: 5500000 },
    { segment: "Bonds", value: 3200000 }
  ],
  config: {
    xAxisKey: "segment",
    title: "Portfolio Allocation",
    description: "Current investment distribution",
    totalLabel: "Total Assets"
  },
  chartConfig: {
    equities: { label: "Equities" },
    bonds: { label: "Bonds" }
  }
}

Always:
- Generate real, contextually appropriate data
- Use proper financial formatting
- Include relevant trends and insights
- Structure data exactly as needed for the chosen chart type
- Choose the most appropriate visualization for the data

Never:
- Use placeholder or static data
- Announce the tool usage
- Include technical implementation details in responses
- NEVER SAY you are using the generate_graph_data tool, just execute it when needed.

Focus on clear financial insights and let the visualization enhance understanding.`,
    });

    console.log("✅ Model API response received:", {
      status: "success",
      stopReason: response.stop_reason,
      hasToolUse: response.content.some((c) => c.type === "tool_use"),
      contentTypes: response.content.map((c) => c.type),
      contentLength:
        response.content[0].type === "text"
          ? response.content[0].text.length
          : 0,
      toolOutput: response.content.find((c) => c.type === "tool_use")
        ? JSON.stringify(
            response.content.find((c) => c.type === "tool_use"),
            null,
            2,
          )
        : "No tool used",
    });

    const toolUseContent = response.content.find((c) => c.type === "tool_use");
    const textContent = response.content.find((c) => c.type === "text");

    const processToolResponse = (toolUseContent: any) => {
      if (!toolUseContent) return null;

      const chartData = validateChartData(toolUseContent.input);

      // Transform data for pie charts to match expected structure
      if (chartData.chartType === "pie") {
        // Ensure data items have 'segment' and 'value' keys
        chartData.data = chartData.data.map((item) => {
          // Find the first key in chartConfig (e.g., 'sales')
          const valueKey = Object.keys(chartData.chartConfig)[0];
          const segmentKey = chartData.config.xAxisKey || "segment";

          return {
            segment: getPieSegment(item, segmentKey),
            value: item[valueKey] ?? item.value,
          };
        });

        // Ensure xAxisKey is set to 'segment' for consistency
        chartData.config.xAxisKey = "segment";
      }

      // Create new chartConfig with system color variables
      const processedChartConfig = Object.entries(chartData.chartConfig).reduce(
        (acc, [key, config], index) => ({
          ...acc,
          [key]: {
            ...config,
            // Assign color variables sequentially
            color: `hsl(var(--chart-${index + 1}))`,
          },
        }),
        {},
      );

      return {
        ...chartData,
        chartConfig: processedChartConfig,
      };
    };

    const processedChartData = toolUseContent
      ? processToolResponse(toolUseContent)
      : null;

    return new Response(
      JSON.stringify({
        content: textContent?.text || "",
        hasToolUse: response.content.some((c) => c.type === "tool_use"),
        toolUse: toolUseContent,
        chartData: processedChartData,
      }),
      {
        headers: {
          "Content-Type": "application/json",
          "Cache-Control": "no-cache",
        },
      },
    );
  } catch (error) {
    console.error("❌ Finance API Error: ", error);
    console.error("Full error details:", {
      name: error instanceof Error ? error.name : "Unknown",
      message: error instanceof Error ? error.message : "Unknown error",
      stack: error instanceof Error ? error.stack : undefined,
      headers: error instanceof Error ? (error as any).headers : undefined,
      response: error instanceof Error ? (error as any).response : undefined,
    });

    if (error instanceof ChartValidationError) {
      return new Response(
        JSON.stringify({
          error: "Invalid chart data",
          details: error.message,
        }),
        {
          status: 422,
          headers: { "Content-Type": "application/json" },
        },
      );
    }

    // Add specific error handling for different scenarios
    if (error instanceof Anthropic.APIError) {
      return new Response(
        JSON.stringify({
          error: "API Error",
          details: error.message,
          code: error.status,
        }),
        { status: error.status },
      );
    }

    if (error instanceof Anthropic.AuthenticationError) {
      return new Response(
        JSON.stringify({
          error: "Authentication Error",
          details: "Invalid API key or authentication failed",
        }),
        { status: 401 },
      );
    }

    return new Response(
      JSON.stringify({
        error:
          error instanceof Error ? error.message : "An unknown error occurred",
      }),
      {
        status: 500,
        headers: { "Content-Type": "application/json" },
      },
    );
  }
}
