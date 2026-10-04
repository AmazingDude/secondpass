// Run with playwright-cli's run-code --filename (see CONTRIBUTING.md).
// eslint-disable-next-line no-unused-expressions -- Playwright CLI evaluates this callback expression.
async (page) => {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  const finding = {
    finding_type: "command_injection", evidence: "Historical review evidence",
    confidence: 90, suggested_fix: "Use arguments", detection_method: "static_rule",
  };
  const review = (id) => {
    const filePath = id === 17 ? "historical.py" : "recent.py";
    const evidence = id === 17 ? finding.evidence : "Unrelated recent evidence";
    const findings = [{ ...finding, evidence }];
    return {
      id, file_path: filePath, worker_name: "security", job_id: null,
      created_at: "2026-10-01T00:01:00Z", gate_threshold: 80,
      accepted_count: 1, needs_review_count: 0,
      gate_result: { accepted: findings, needs_review: [], threshold: 80 },
      review_result: { findings, file_path: filePath, worker_name: "security", coverage_status: "inconclusive" },
    };
  };
  // Only the newest review is discoverable in the recent list. The explicit
  // historical ID must be fetched independently, never silently substituted.
  await page.route("http://127.0.0.1:8000/**", async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() !== "GET") throw new Error("This regression must not write outcomes");
    let body;
    if (url.pathname === "/reviews/17") body = review(17);
    else if (url.pathname === "/reviews/99") body = review(99);
    else if (url.pathname === "/reviews") body = { reviews: [review(99)] };
    else if (url.pathname === "/outcomes") body = { file_path: url.searchParams.get("file_path"), outcomes: [] };
    else throw new Error(`Unexpected fixture request: ${url.pathname}`);
    await route.fulfill({ status: 200, headers: { "Access-Control-Allow-Origin": "*" }, contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto("http://127.0.0.1:5174/#/reviews/17");
  await page.getByText("Historical review evidence", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Memory", exact: true }).click();
  await page.getByRole("heading", { name: "Verified outcomes" }).waitFor();
  await page.getByText("Historical review evidence", { exact: true }).waitFor({ timeout: 3000 });
  await page.waitForURL("**/#/memory/reviews/17");
  const picker = page.getByRole("button", { name: "Review result", exact: true });
  if (!(await picker.innerText()).includes("#17")) throw new Error("Historical review missing from picker");
  if (await page.getByText("Unrelated recent evidence", { exact: true }).count()) throw new Error("Memory substituted an unrelated review");
  await page.reload();
  await page.getByText("Historical review evidence", { exact: true }).waitFor();
  await picker.click();
  await page.getByRole("option").filter({ hasText: "#99" }).click();
  await page.getByText("Unrelated recent evidence", { exact: true }).waitFor();
  await page.waitForURL("**/#/memory/reviews/99");
  await page.reload();
  await page.getByText("Unrelated recent evidence", { exact: true }).waitFor();
  await page.goBack();
  await page.getByText("Historical review evidence", { exact: true }).waitFor();
  await page.goForward();
  await page.getByText("Unrelated recent evidence", { exact: true }).waitFor();
}
