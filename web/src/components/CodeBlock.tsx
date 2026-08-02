import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";

/** Light theme tokens aligned with DESIGN.md §5.4a / §2. */
const secondpassPrism = {
  'code[class*="language-"]': {
    color: "var(--text-primary)",
    background: "none",
    fontFamily: "var(--font-mono)",
    fontSize: "0.82rem",
    textAlign: "left" as const,
    whiteSpace: "pre" as const,
    wordSpacing: "normal",
    wordBreak: "normal" as const,
    lineHeight: "1.45",
    tabSize: 4,
  },
  'pre[class*="language-"]': {
    color: "var(--text-primary)",
    background: "transparent",
    fontFamily: "var(--font-mono)",
    fontSize: "0.82rem",
    margin: 0,
    padding: 0,
    overflow: "auto",
  },
  comment: { color: "var(--text-secondary)", fontStyle: "italic" },
  prolog: { color: "var(--text-secondary)" },
  doctype: { color: "var(--text-secondary)" },
  cdata: { color: "var(--text-secondary)" },
  punctuation: { color: "var(--text-secondary)" },
  property: { color: "var(--text-primary)" },
  tag: { color: "var(--accent-blue)" },
  boolean: { color: "var(--accent-orange)" },
  number: { color: "var(--accent-orange)" },
  constant: { color: "var(--accent-orange)" },
  symbol: { color: "var(--accent-orange)" },
  deleted: { color: "var(--diff-remove-text)" },
  selector: { color: "var(--severity-low)" },
  "attr-name": { color: "var(--accent-blue)" },
  string: { color: "var(--severity-low)" },
  char: { color: "var(--severity-low)" },
  builtin: { color: "var(--accent-blue)" },
  inserted: { color: "var(--diff-add-text)" },
  operator: { color: "var(--text-secondary)" },
  entity: { color: "var(--accent-blue)" },
  url: { color: "var(--accent-blue)" },
  ".language-css .token.string": { color: "var(--severity-low)" },
  ".style .token.string": { color: "var(--severity-low)" },
  atrule: { color: "var(--accent-blue)" },
  "attr-value": { color: "var(--severity-low)" },
  keyword: { color: "var(--accent-blue)", fontWeight: "600" },
  function: { color: "var(--text-primary)", fontWeight: "700" },
  class_name: { color: "var(--text-primary)", fontWeight: "700" },
  regex: { color: "var(--severity-medium)" },
  important: { color: "var(--severity-high)", fontWeight: "700" },
  variable: { color: "var(--text-primary)" },
};

function languageFromPath(path?: string): string {
  if (!path) return "python";
  const lower = path.toLowerCase();
  if (lower.endsWith(".ts") || lower.endsWith(".tsx")) return "tsx";
  if (lower.endsWith(".js") || lower.endsWith(".jsx")) return "jsx";
  if (lower.endsWith(".json")) return "json";
  if (lower.endsWith(".go")) return "go";
  if (lower.endsWith(".rs")) return "rust";
  if (lower.endsWith(".md")) return "markdown";
  return "python";
}

export type TextSegment = { kind: "text" | "code"; value: string };

/**
 * Split on paired backticks. Outside = always plain text; inside = code only.
 * Unmatched trailing backtick is treated as plain text.
 */
export function splitBacktickSegments(text: string): TextSegment[] {
  const segments: TextSegment[] = [];
  let i = 0;
  while (i < text.length) {
    const open = text.indexOf("`", i);
    if (open === -1) {
      segments.push({ kind: "text", value: text.slice(i) });
      break;
    }
    if (open > i) {
      segments.push({ kind: "text", value: text.slice(i, open) });
    }
    const close = text.indexOf("`", open + 1);
    if (close === -1) {
      segments.push({ kind: "text", value: text.slice(open) });
      break;
    }
    segments.push({ kind: "code", value: text.slice(open + 1, close) });
    i = close + 1;
  }
  return segments.filter((s) => s.value.length > 0);
}

/** True when text is a whole-block code/diff snippet (no backtick prose mix). */
export function looksLikeCode(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed) return false;
  if (/```/.test(trimmed)) return true;
  if (trimmed.includes("`")) return false;

  const lines = trimmed.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  if (lines.length === 0) return false;

  const codeLine = (s: string) =>
    /^(def |class |async def |import |from .+ import |return |const |let |var |function |if |for |while |elif |else:)/.test(
      s,
    ) ||
    /^[+\-](?![+\-]).*[=(){}[\];]/.test(s) ||
    (/[=(){}[\];]|=>/.test(s) &&
      !/^(the |a |an |this |that |implement |consider |refactor |ensure |replace |add |remove )/i.test(
        s,
      ));

  const hits = lines.filter(codeLine).length;
  if (hits === 0) return false;
  if (lines.length === 1 && codeLine(lines[0])) return true;
  return hits / lines.length >= 0.4;
}

function HighlightedCode({
  code,
  language,
  inline,
}: {
  code: string;
  language: string;
  inline?: boolean;
}) {
  return (
    <SyntaxHighlighter
      language={language}
      style={secondpassPrism}
      PreTag={inline ? "span" : "div"}
      customStyle={
        inline
          ? {
              margin: 0,
              padding: "0.05rem 0.25rem",
              background: "color-mix(in srgb, var(--surface) 55%, transparent)",
              borderRadius: 4,
              display: "inline",
              fontSize: "0.88em",
            }
          : {
              margin: 0,
              padding: "0.85rem 0.95rem",
              background: "transparent",
            }
      }
      codeTagProps={{
        style: {
          fontFamily: "var(--font-mono)",
          fontSize: inline ? "0.88em" : "0.82rem",
          whiteSpace: inline ? "pre-wrap" : "pre-wrap",
          wordBreak: "break-word",
        },
      }}
    >
      {code}
    </SyntaxHighlighter>
  );
}

type Props = {
  code: string;
  language?: string;
  filePath?: string;
  className?: string;
  /** auto = backtick-aware mix / whole-block code / plain prose */
  mode?: "auto" | "code" | "prose";
};

/** Shared block for Findings + Memory. Keyword color never applies outside `…`. */
export function CodeBlock({
  code,
  language,
  filePath,
  className,
  mode = "auto",
}: Props) {
  const lang = language || languageFromPath(filePath);
  const hasBackticks = code.includes("`");

  if (mode === "prose" || (mode === "auto" && !hasBackticks && !looksLikeCode(code))) {
    return (
      <div className={["code-block", "code-block-prose", className].filter(Boolean).join(" ")}>
        <p className="prose-block">{code}</p>
      </div>
    );
  }

  if (mode === "code" || (mode === "auto" && !hasBackticks && looksLikeCode(code))) {
    return (
      <div className={["code-block", className].filter(Boolean).join(" ")}>
        <HighlightedCode code={code} language={lang} />
      </div>
    );
  }

  // Mixed prose + `inline code`: only highlight inside backticks.
  const segments = splitBacktickSegments(code);
  return (
    <div
      className={["code-block", "code-block-prose", className]
        .filter(Boolean)
        .join(" ")}
    >
      <p className="prose-block prose-mixed">
        {segments.map((seg, idx) =>
          seg.kind === "text" ? (
            <span key={idx}>{seg.value}</span>
          ) : (
            <HighlightedCode
              key={idx}
              code={seg.value}
              language={lang}
              inline
            />
          ),
        )}
      </p>
    </div>
  );
}
