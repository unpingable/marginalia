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
fs.mkdirSync(screenshots, {recursive: true});

const results = {
  phase: "browser acceptance before restart",
  started_at: new Date().toISOString(),
  base_url: baseURL,
  viewport: {width: 1440, height: 1000},
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

async function sendPrompt(page, prompt, timeout = 240_000) {
  const before = await page.locator(".message.assistant").count();
  await page.locator("#prompt").fill(prompt);
  await page.locator("#send").click();
  await page.waitForFunction(
    (prior) => {
      const replies = document.querySelectorAll(".message.assistant").length;
      return replies > prior && !document.querySelector(".message-body.thinking");
    },
    before,
    {timeout},
  );
  await page.waitForTimeout(900);
  return page.locator(".message.assistant").last();
}

async function addCharacter(page, name, description, voice, wont) {
  await page.locator('[data-toggle-form="character-form"]').click();
  await page.locator("#character-name").fill(name);
  await page.locator("#character-description").fill(description);
  await page.locator("#character-voice").fill(voice);
  await page.locator("#character-wont").fill(wont);
  await page.locator("#character-form .primary-button").click();
  await page.locator(`#characters .item-name`, {hasText: name}).waitFor();
}

async function addRule(page, rule) {
  await page.locator('[data-toggle-form="rule-form"]').click();
  await page.locator("#rule-text").fill(rule);
  await page.locator("#rule-form .primary-button").click();
  await page.locator("#world-rules .item-copy", {hasText: rule}).waitFor();
}

async function addBoundary(page, description, patterns) {
  await page.locator('[data-toggle-form="forbidden-form"]').click();
  await page.locator("#forbidden-description").fill(description);
  await page.locator("#forbidden-patterns").fill(patterns);
  await page.locator("#forbidden-form .primary-button").click();
  await page.locator("#forbidden .item-copy", {hasText: description}).waitFor();
}

const browser = await chromium.launch({
  headless: true,
  executablePath: chromePath,
  args: ["--no-sandbox"],
});
const context = await browser.newContext({
  viewport: results.viewport,
  acceptDownloads: true,
});
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
  check("intentional empty state", await page.getByText("What are we writing?").isVisible());
  check("no operator vocabulary on landing page", !/(Phosphor|Desk|Intent Compiler|receipt hash|regime)/i.test(await page.locator("body").innerText()));
  await screenshot(page, "01-empty-writing-room.png");

  await page.locator("#edit-project").click();
  await page.locator("#project-settings[open]").waitFor();
  const saveButtonCount = await page.locator("#project-settings #save-project").count();
  results.observations.project_settings_save_button_count = saveButtonCount;
  check("project settings has one save action", saveButtonCount === 1, {save_button_count: saveButtonCount});
  await screenshot(page, "02-project-direction.png");
  await page.locator("#project-brief").fill(
    "A literary mystery about a municipal archive whose clocks erase one memory at midnight.",
  );
  await page.locator("#collaborator-stance").fill(
    "Act as a continuity-conscious developmental collaborator. Propose durable facts; never treat them as accepted canon without my approval.",
  );
  await page.locator("#voice-guidance").fill(
    "Close third person, precise physical detail, dry humor, restrained dialogue, and no ornamental metaphors.",
  );
  await page.locator("#save-project").first().click();
  await page.locator("#project-settings").waitFor({state: "hidden"});
  await page.getByText("A literary mystery about a municipal archive", {exact: false}).waitFor();
  check("project direction saves through ordinary UI", true);

  await page.locator("#new-session").click();
  await page.locator("#sessions .session").first().waitFor();
  check("conversation can be created and opened", await page.locator("#sessions .session").first().isVisible());

  const longPrompt = [
    "Write an 800-word opening scene for this project.",
    "Use the Markdown heading `## The Archive Clock`, include the phrase `*almost remembered*` in italics,",
    "and make the warning **YOUR MEMORY IS SYNCING** bold.",
    "Use several short paragraphs and one brief blockquote from an archive notice.",
    "Do not add or propose any story-bible entries in this response.",
  ].join(" ");
  const longReply = await sendPrompt(page, longPrompt);
  const responseText = await longReply.locator(".message-body").innerText();
  check("fiction response generated", responseText.length > 500, {characters: responseText.length});
  check("Markdown heading rendered", await longReply.locator("h2").count() > 0);
  check("Markdown emphasis rendered", await longReply.locator("em").count() > 0);
  check("Markdown strong text rendered", await longReply.locator("strong").count() > 0);
  await screenshot(page, "03-long-markdown-response.png");

  const layout = await page.evaluate(() => {
    const composer = document.querySelector(".composer-wrap").getBoundingClientRect();
    const messages = document.querySelector("#messages");
    const prompt = document.querySelector("#prompt").getBoundingClientRect();
    return {
      viewport_height: window.innerHeight,
      composer_top: composer.top,
      composer_bottom: composer.bottom,
      prompt_height: prompt.height,
      message_scroll_height: messages.scrollHeight,
      message_client_height: messages.clientHeight,
      send_visible: Boolean(document.querySelector("#send")?.offsetParent),
    };
  });
  results.observations.long_response_layout = layout;
  check(
    "composer remains visible after long response",
    layout.composer_top >= 0 && layout.composer_bottom <= layout.viewport_height && layout.send_visible,
    layout,
  );

  page.once("dialog", (dialog) => dialog.accept("Archive Clock — opening"));
  await longReply.getByRole("button", {name: "Save as draft"}).click();
  await page.locator("#artifact-editor.visible").waitFor();
  check(
    "Save as draft creates an editable draft",
    (await page.locator("#artifact-title").inputValue()) === "Archive Clock — opening"
      && (await page.locator("#artifact-content").inputValue()).includes("The Archive Clock"),
  );
  await screenshot(page, "04-saved-draft.png");

  await page.locator('[data-workspace-tab="bible"]').click();
  await addCharacter(
    page,
    "Nadia Quill",
    "The night archivist who hears erased memories as clockwork.",
    "Exact, dry, and reluctant to explain herself.",
    "destroy an original record",
  );
  check("manual character appears in Story bible", await page.getByText("Nadia Quill", {exact: true}).isVisible());

  for (const [name, description] of [
    ["Elias Wren", "A retired horologist who distrusts digital clocks."],
    ["June Mercer", "The deputy mayor who funded the archive restoration."],
    ["Tomas Grey", "A courier who remembers deliveries nobody made."],
    ["Priya Sen", "A conservator cataloguing the building's sealed rooms."],
    ["Milo North", "The overnight guard and an incurable gossip."],
  ]) {
    await addCharacter(page, name, description, "Distinct and understated.", "lie about the archive fire");
  }
  for (const rule of [
    "At midnight, one archive clock erases a witnessed memory.",
    "Paper records retain traces that digital copies lose.",
    "No clock in the archive shows the same time twice.",
    "A recovered memory always belongs partly to someone else.",
  ]) await addRule(page, rule);
  for (const [description, patterns] of [
    ["Nadia never destroys an original record.", "destroy the original,burn the ledger"],
    ["The archive cannot solve a problem through time travel.", "time travel,go back in time"],
    ["The clocks never speak in a human voice.", "the clock said,the clock whispered"],
    ["The mystery cannot be dismissed as a dream.", "it was all a dream,just a dream"],
  ]) await addBoundary(page, description, patterns);

  const railMetrics = await page.locator(".workspace-scroll").evaluate((node) => ({
    client_height: node.clientHeight,
    scroll_height: node.scrollHeight,
    client_width: node.clientWidth,
    scroll_width: node.scrollWidth,
  }));
  results.observations.populated_story_bible_layout = railMetrics;
  check("populated Story bible does not overflow horizontally", railMetrics.scroll_width <= railMetrics.client_width + 1, railMetrics);
  await page.locator(".workspace-scroll").evaluate((node) => { node.scrollTop = 0; });
  await screenshot(page, "05-populated-story-bible-top.png");
  await page.locator(".workspace-scroll").evaluate((node) => { node.scrollTop = node.scrollHeight; });
  await screenshot(page, "06-populated-story-bible-bottom.png");

  const characterCountBeforeProposal = await page.locator("#characters .item-card").count();
  const proposalPrompt = [
    "Please add a new character named Orla Finch to the story bible.",
    "She is a retired radio engineer who can hear the archive clocks through disconnected speakers.",
    "Her voice is brisk, practical, and unexpectedly tender.",
    "She would never surrender a recording to the mayor.",
    "If you cannot directly change the story bible, make this a clear proposal and include a line beginning `Character:`.",
  ].join(" ");
  await sendPrompt(page, proposalPrompt);
  await page.waitForTimeout(1200);
  const characterCountAfterProposal = await page.locator("#characters .item-card").count();
  const suggestionVisible = await page.locator("#suggestion-group").isVisible();
  const suggestionCount = await page.locator("#suggestions .item-card").count();
  const suggestionActions = suggestionVisible
    ? await page.locator("#suggestions button").allTextContents()
    : [];
  results.observations.canon_proposal = {
    character_count_before: characterCountBeforeProposal,
    character_count_after: characterCountAfterProposal,
    suggestion_visible: suggestionVisible,
    suggestion_count: suggestionCount,
    actions: suggestionActions,
  };
  check(
    "chat cannot silently mutate Story bible",
    characterCountAfterProposal === characterCountBeforeProposal,
    results.observations.canon_proposal,
  );
  check("chat produces a reviewable canon proposal", suggestionVisible && suggestionCount > 0, results.observations.canon_proposal);
  check("canon proposal offers edit before acceptance", suggestionActions.some((label) => /edit/i.test(label)), {actions: suggestionActions});
  await page.locator(".workspace-scroll").evaluate((node) => { node.scrollTop = node.scrollHeight; });
  await screenshot(page, "07-chat-canon-proposal.png");

  const downloadPromise = page.waitForEvent("download");
  await page.locator("#export-project").click();
  const download = await downloadPromise;
  const exportPath = path.join(evidenceRoot, "acceptance-export.json");
  await download.saveAs(exportPath);
  const exported = JSON.parse(fs.readFileSync(exportPath, "utf8"));
  results.observations.export = {
    suggested_filename: download.suggestedFilename(),
    project_context: exported.project?.context_id,
    conversations: exported.conversations?.length || 0,
    characters: exported.story_bible?.characters?.length || 0,
    artifacts: exported.artifacts?.length || 0,
  };
  check(
    "project export contains writing state",
    results.observations.export.conversations > 0
      && results.observations.export.characters >= 6
      && results.observations.export.artifacts > 0,
    results.observations.export,
  );

  for (const route of ["/dashboard", "/docs", "/openapi.json", "/v2/runs", "/governor/status"]) {
    const response = await page.request.get(`${baseURL}${route}`);
    check(`operator route stays unreachable: ${route}`, response.status() === 404, {status: response.status()});
  }

  const title = await page.locator("#sessions .session").first().innerText();
  results.observations.generated_conversation_title = title;
  check("generated conversation title is bounded", title.length <= 52, {title, length: title.length});

  const compact = await context.newPage();
  await compact.setViewportSize({width: 1024, height: 768});
  await compact.goto(baseURL, {waitUntil: "networkidle"});
  const workspaceVisible = await compact.locator("#workspace").isVisible();
  const visibleWorkspaceOpeners = await compact.locator(
    'button:visible:has-text("Story bible"), button:visible:has-text("Project"), button:visible:has-text("Drafts")',
  ).count();
  results.observations.compact_layout = {workspace_visible: workspaceVisible, visible_workspace_openers: visibleWorkspaceOpeners};
  check("compact layout retains a discoverable project-workspace opener", workspaceVisible || visibleWorkspaceOpeners > 0, results.observations.compact_layout);
  await screenshot(compact, "08-compact-1024px.png");
  await compact.close();
} catch (error) {
  results.fatal_error = {message: error.message, stack: error.stack};
  try { await screenshot(page, "99-fatal-state.png"); } catch {}
} finally {
  results.finished_at = new Date().toISOString();
  fs.writeFileSync(
    path.join(evidenceRoot, "phase1-results.json"),
    `${JSON.stringify(results, null, 2)}\n`,
    "utf8",
  );
  await browser.close();
}

if (results.fatal_error) process.exitCode = 1;
