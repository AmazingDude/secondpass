/** Shared stopping condition for polling and live progress presentation. */
export function isTerminalJobStatus(status: string | undefined): boolean {
  return status === "completed" || status === "failed" || status === "interrupted";
}
