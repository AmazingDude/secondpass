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

/** True when text looks like a code snippet / diff, not plain-English guidance. */
export function looksLikeCode(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed) return false;
  if (/```/.test(trimmed)) return true;

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
  // Single short code-ish line (e.g. `return NOTES.get(note_id)`)
  if (lines.length === 1 && codeLine(lines[0])) return true;
  return hits / lines.length >= 0.4;
}

type Props = {
  code: string;
  language?: string;
  filePath?: string;
  className?: string;
  /** auto = highlight only when looksLikeCode; code = always; prose = never */
  mode?: "auto" | "code" | "prose";
};

/** Shared block for Findings + Memory — prose by default for NL suggested_fix. */
export function CodeBlock({
  code,
  language,
  filePath,
  className,
  mode = "auto",
}: Props) {
  const asCode =
    mode === "code" || (mode === "auto" && looksLikeCode(code));
  const lang = language || languageFromPath(filePath);
  const wrapClass = ["code-block", !asCode ? "code-block-prose" : "", className]
    .filter(Boolean)
    .join(" ");

  if (!asCode) {
    return (
      <div className={wrapClass}>
        <p className="prose-block">{code}</p>
      </div>
    );
  }

  return (
    <div className={wrapClass}>
      <SyntaxHighlighter
        language={lang}
        style={secondpassPrism}
        PreTag="div"
        customStyle={{
          margin: 0,
          padding: "0.85rem 0.95rem",
          background: "transparent",
        }}
        codeTagProps={{
          style: {
            fontFamily: "var(--font-mono)",
            fontSize: "0.82rem",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          },
        }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
}
