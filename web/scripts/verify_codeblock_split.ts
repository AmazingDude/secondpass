/**
 * Offline verify for backtick split + mixed suggested_fix (no React).
 * Run: npx --yes tsx scripts/verify_codeblock_split.ts
 */
import {
  looksLikeCode,
  splitBacktickSegments,
} from "../src/components/CodeBlock";

const notesIdorSuggested =
  "Implement an ownership check if the current_user_id matches " +
  "note['owner_id'], such as `if note['owner_id'] != current_user_id: " +
  "raise PermissionError` before `return NOTES.get(note_id)`.";

const segments = splitBacktickSegments(notesIdorSuggested);
const codeParts = segments.filter((s) => s.kind === "code").map((s) => s.value);
const textParts = segments.filter((s) => s.kind === "text").map((s) => s.value);

console.log("segments:", JSON.stringify(segments, null, 2));

const fail = (msg: string): never => {
  console.error("FAIL:", msg);
  throw new Error(msg);
};

if (codeParts.length !== 2) {
  fail(`expected 2 code spans, got ${codeParts.length}`);
}
if (!codeParts[0].includes("owner_id") || !codeParts[0].startsWith("if ")) {
  fail(`unexpected first code span: ${codeParts[0]}`);
}
if (!codeParts[1].includes("NOTES.get")) {
  fail(`unexpected second code span: ${codeParts[1]}`);
}
for (const t of textParts) {
  // Prose must keep English "if" / "as" — those must NOT be classified as code.
  if (t.includes("`")) fail(`text segment still has backtick: ${t}`);
}
if (!textParts.some((t) => /\bif\b/.test(t))) {
  fail("expected English 'if' to remain in a text segment");
}
if (!textParts.some((t) => /\bas\b/.test(t))) {
  fail("expected English 'as' to remain in a text segment");
}

const pureProse =
  "Add an ownership check comparing owner_id to current_user_id before returning.";
if (looksLikeCode(pureProse)) fail("pure prose must not lookLikeCode");
if (looksLikeCode(notesIdorSuggested)) {
  fail("backtick-mixed text must not take whole-string looksLikeCode path");
}

console.log("OK: backtick split isolates code; English if/as stay in text segments");
