import "server-only";

import { createHash } from "crypto";
import { readdir, readFile } from "fs/promises";
import path from "path";
import type { RAGSource } from "@/app/lib/utils";

interface KnowledgeChunk {
  id: string;
  fileName: string;
  content: string;
  terms: Set<string>;
}

const KNOWLEDGE_DIRECTORY = path.join(process.cwd(), "knowledge");
const SUPPORTED_EXTENSIONS = new Set([".md", ".markdown", ".txt"]);
const MAX_CHUNK_LENGTH = 1200;
let knowledgeCache: Promise<KnowledgeChunk[]> | undefined;

function tokenize(text: string): Set<string> {
  const normalized = text.toLowerCase();
  const terms = new Set(normalized.match(/[a-z0-9][a-z0-9_-]+/g) ?? []);
  for (const segment of normalized.match(/[\u3400-\u9fff]+/g) ?? []) {
    if (segment.length === 1) terms.add(segment);
    for (let index = 0; index < segment.length - 1; index += 1) {
      terms.add(segment.slice(index, index + 2));
    }
  }
  return terms;
}

function splitIntoChunks(content: string): string[] {
  const sections = content
    .replace(/\r\n/g, "\n")
    .split(/\n{2,}/)
    .map((section) => section.trim())
    .filter(Boolean);
  const chunks: string[] = [];
  let current = "";

  for (const section of sections) {
    if (current && current.length + section.length + 2 > MAX_CHUNK_LENGTH) {
      chunks.push(current);
      current = "";
    }
    if (section.length > MAX_CHUNK_LENGTH) {
      if (current) chunks.push(current);
      for (let start = 0; start < section.length; start += MAX_CHUNK_LENGTH) {
        chunks.push(section.slice(start, start + MAX_CHUNK_LENGTH));
      }
      current = "";
    } else {
      current = current ? `${current}\n\n${section}` : section;
    }
  }
  if (current) chunks.push(current);
  return chunks;
}

async function listKnowledgeFiles(directory: string): Promise<string[]> {
  const entries = await readdir(directory, { withFileTypes: true });
  const nestedFiles = await Promise.all(
    entries.map(async (entry) => {
      const entryPath = path.join(directory, entry.name);
      if (entry.isDirectory()) return listKnowledgeFiles(entryPath);
      return SUPPORTED_EXTENSIONS.has(path.extname(entry.name).toLowerCase())
        ? [entryPath]
        : [];
    }),
  );
  return nestedFiles.flat().sort();
}

async function loadKnowledgeChunks(): Promise<KnowledgeChunk[]> {
  let files: string[];
  try {
    files = await listKnowledgeFiles(KNOWLEDGE_DIRECTORY);
  } catch (error) {
    console.error(`Unable to read local knowledge directory: ${KNOWLEDGE_DIRECTORY}`, error);
    return [];
  }

  const chunks = await Promise.all(
    files.map(async (filePath) => {
      const relativePath = path.relative(KNOWLEDGE_DIRECTORY, filePath);
      const content = await readFile(filePath, "utf8");
      return splitIntoChunks(content).map((chunk, index) => ({
        id: createHash("sha1")
          .update(`${relativePath}:${index}:${chunk}`)
          .digest("hex")
          .slice(0, 16),
        fileName: relativePath,
        content: chunk,
        terms: tokenize(`${relativePath} ${chunk}`),
      }));
    }),
  );
  return chunks.flat();
}

function scoreChunk(query: string, queryTerms: Set<string>, chunk: KnowledgeChunk): number {
  if (queryTerms.size === 0) return 0;
  let matches = 0;
  queryTerms.forEach((term) => {
    if (chunk.terms.has(term)) matches += term.length > 2 ? 2 : 1;
  });
  const normalizedQuery = query.trim().toLowerCase();
  const exactPhraseBonus =
    normalizedQuery.length >= 3 && chunk.content.toLowerCase().includes(normalizedQuery)
      ? 5
      : 0;
  return (matches + exactPhraseBonus) / Math.sqrt(queryTerms.size * chunk.terms.size);
}

export async function retrieveContext(
  query: string,
  _knowledgeBaseId?: string,
  n: number = 3,
): Promise<{ context: string; isRagWorking: boolean; ragSources: RAGSource[] }> {
  try {
    knowledgeCache ??= loadKnowledgeChunks();
    const chunks = await knowledgeCache;
    const queryTerms = tokenize(query);
    const matches = chunks
      .map((chunk) => ({ chunk, score: scoreChunk(query, queryTerms, chunk) }))
      .filter(({ score }) => score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, Math.max(1, n));
    const ragSources = matches.map(({ chunk, score }) => ({
      id: chunk.id,
      fileName: chunk.fileName,
      snippet: chunk.content,
      score: Number(score.toFixed(4)),
    }));

    return {
      context: matches
        .map(({ chunk }) => `[来源：${chunk.fileName}]\n${chunk.content}`)
        .join("\n\n---\n\n"),
      isRagWorking: matches.length > 0,
      ragSources,
    };
  } catch (error) {
    console.error("Local knowledge retrieval error:", error);
    return { context: "", isRagWorking: false, ragSources: [] };
  }
}
