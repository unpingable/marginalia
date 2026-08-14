import fs from "node:fs";
import path from "node:path";
import {createRequire} from "node:module";

const require = createRequire(import.meta.url);
const playwrightRoot = process.env.PLAYWRIGHT_ROOT
  || "/home/jbeck/.npm/_npx/e41f203b7505f1fb/node_modules/playwright";
const {chromium} = require(playwrightRoot);

const baseURL = process.env.MARGINALIA_URL || "http://localhost:8011";
const chromePath = process.env.CHROMIUM_PATH
  || "/home/jbeck/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome";
const evidenceRoot = path.resolve("research/m1.5-acceptance");
const screenshots = path.join(evidenceRoot, "screenshots");

const expected = {
  brief: "A literary mystery about a municipal archive whose clocks erase one memory at midnight.",
  stance: "Act as a continuity-conscious developmental collaborator. Propose durable facts; never treat them as accepted canon without my approval.",
  voice: "Close third person, precise physical detail, dry humor, restrained dialogue, and no ornamental metaphors.",
};

const results = {
  phase: "browser acceptance after container restart",
  started_at: new Date().toISOString(),
  base_url: baseURL,
  checks: [],
  console_errors: [],
  failed_responses: [],
  observations: {},
};

function check(name, passed, details = {}) {
  results.checks.push({name, passed: Boolean(passed), details});
}

async function screenshot(page, name) {
  await page.screenshot({path: path.join(screenshots, name), fullPage: true});
}

const browser = await chromium.launch({
  headless: true,
  executablePath: chromePath,
  args: ["--no-sandbox"],
});
const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
const page = await context.newPage();
page.on("console", (message) => {
  if (message.type() === "error") results.console_errors.push(message.text());
});
page.on("pageerror", (error) => results.console_errors.push(error.message));
page.on("response", (response) => {
  if (response.status() >= 400) {
    results.failed_responses.push({status: response.status(), url: response.url()});
  }
});

try {
  await page.goto(baseURL, {waitUntil: "networkidle"});
  await page.locator("#provider-name", {hasText: "Codex"}).waitFor();

  await page.locator("#edit-project").click();
  await page.locator("#project-settings[open]").waitFor();
  const persistedDirection = {
    brief: await page.locator("#project-brief").inputValue(),
    stance: await page.locator("#collaborator-stance").inputValue(),
    voice: await page.locator("#voice-guidance").inputValue(),
  };
  results.observations.project_direction = persistedDirection;
  check(
    "project direction survives container restart",
    persistedDirection.brief === expected.brief
      && persistedDirection.stance === expected.stance
      && persistedDirection.voice === expected.voice,
    persistedDirection,
  );
  await page.locator("#close-project").click();

  const sessionCount = await page.locator("#sessions .session").count();
  const sessionTitle = sessionCount
    ? await page.locator("#sessions .session").first().innerText()
    : "";
  check("conversation survives container restart", sessionCount === 1, {session_count: sessionCount, title: sessionTitle});

  await page.locator("#sessions .session").first().click();
  await page.locator(".message.assistant").first().waitFor();
  const persistedChat = {
    user_messages: await page.locator(".message.user").count(),
    assistant_messages: await page.locator(".message.assistant").count(),
    headings: await page.locator(".message.assistant h2").count(),
    emphasis: await page.locator(".message.assistant em").count(),
    strong: await page.locator(".message.assistant strong").count(),
  };
  results.observations.chat = persistedChat;
  check(
    "conversation messages and rendered Markdown survive restart",
    persistedChat.user_messages === 2
      && persistedChat.assistant_messages === 2
      && persistedChat.headings > 0
      && persistedChat.emphasis > 0
      && persistedChat.strong > 0,
    persistedChat,
  );

  const storyBible = {
    characters: await page.locator("#characters .item-card").count(),
    rules: await page.locator("#world-rules .item-card").count(),
    boundaries: await page.locator("#forbidden .item-card").count(),
  };
  results.observations.story_bible = storyBible;
  check(
    "accepted Story bible state survives restart",
    storyBible.characters === 6 && storyBible.rules === 4 && storyBible.boundaries === 4,
    storyBible,
  );
  await screenshot(page, "09-after-container-restart.png");

  await page.locator('[data-workspace-tab="drafts"]').click();
  await page.locator("#artifacts .artifact-card").first().waitFor();
  await page.locator("#artifacts .artifact-card").first().click();
  await page.locator("#artifact-editor.visible").waitFor();
  const draft = {
    count: await page.locator("#artifacts .artifact-card").count(),
    title: await page.locator("#artifact-title").inputValue(),
    content_has_heading: (await page.locator("#artifact-content").inputValue()).includes("## The Archive Clock"),
    revision_options: await page.locator("#revision-select option").count(),
  };
  results.observations.draft = draft;
  check(
    "saved draft and revision survive restart",
    draft.count === 1
      && draft.title === "Archive Clock — opening"
      && draft.content_has_heading
      && draft.revision_options === 1,
    draft,
  );
  await screenshot(page, "10-persisted-draft.png");

  const scannerResponse = await page.request.post(`${baseURL}/governor/fiction/capture/scan`, {
    data: {
      text: "Character: Orla Finch — a retired radio engineer who hears archive clocks through disconnected speakers.",
      message_id: "acceptance-after-restart",
    },
  });
  const scannerBody = await scannerResponse.json();
  results.observations.canon_scanner = {status: scannerResponse.status(), body: scannerBody};
  check(
    "runtime canon scanner is available",
    scannerResponse.ok() && !scannerBody.error,
    results.observations.canon_scanner,
  );

  const compact = await context.newPage();
  await compact.setViewportSize({width: 1024, height: 768});
  await compact.goto(baseURL, {waitUntil: "networkidle"});
  const candidateOpeners = await compact.locator(
    'button:visible:has-text("Story bible"), button:visible:has-text("Project"), button:visible:has-text("Drafts")',
  ).evaluateAll((nodes) => nodes.map((node) => {
    const rect = node.getBoundingClientRect();
    return {
      text: node.innerText,
      id: node.id,
      class_name: node.className,
      rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
    };
  }));
  const compactState = {
    workspace_visible: await compact.locator("#workspace").isVisible(),
    candidate_openers: candidateOpeners,
  };
  results.observations.compact_layout = compactState;
  check(
    "compact layout offers an unambiguous Story bible/project control",
    compactState.workspace_visible
      || candidateOpeners.some((item) => /story bible|the project|project settings/i.test(item.text)),
    compactState,
  );
  await screenshot(compact, "11-compact-layout-control-audit.png");
  await compact.close();
} catch (error) {
  results.fatal_error = {message: error.message, stack: error.stack};
  try { await screenshot(page, "99-phase2-fatal-state.png"); } catch {}
} finally {
  results.finished_at = new Date().toISOString();
  fs.writeFileSync(
    path.join(evidenceRoot, "phase2-results.json"),
    `${JSON.stringify(results, null, 2)}\n`,
    "utf8",
  );
  await browser.close();
}

if (results.fatal_error) process.exitCode = 1;
