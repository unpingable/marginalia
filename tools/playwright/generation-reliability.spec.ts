import { expect, Page, test } from '@playwright/test';

async function mockWritingRoom(page: Page, available: boolean, enabled = false) {
  const savedSettings: Array<Record<string, unknown>> = [];
  let currentEnabled = enabled;
  let currentFallback: unknown = null;
  let currentVersion = 0;
  await page.route('**/*', async (route) => {
    const request = route.request();
    if (request.resourceType() === 'document') return route.continue();
    const path = new URL(request.url()).pathname;
    let body: Record<string, unknown> = {};
    if (path === '/v1/workspaces') {
      body = {
        default_workspace_id: 'erin',
        workspaces: [{ id: 'erin', name: 'Erin', default_project_id: 'default' }],
      };
    } else if (path === '/v1/projects') {
      body = {
        workspace_id: 'erin',
        default_project_id: 'default',
        projects: [{ id: 'default', name: 'Novel', context_id: 'test', workspace_id: 'erin' }],
      };
    } else if (path === '/v1/project') {
      body = request.method() === 'PUT' ? JSON.parse(request.postData() || '{}') : {
        version: 1,
        project_brief: '',
        collaborator_stance: '',
        voice_style_guidance: '',
        has_guidance: false,
      };
    } else if (path === '/v1/generation/settings') {
      if (request.method() === 'PUT') {
        const saved = JSON.parse(request.postData() || '{}');
        if (saved.expected_version !== currentVersion) {
          return route.fulfill({
            status: 409,
            contentType: 'application/json',
            body: JSON.stringify({ detail: { message: 'generation settings version conflict' } }),
          });
        }
        savedSettings.push(saved);
        currentEnabled = Boolean(saved.enabled);
        currentFallback = saved.fallback_model;
        currentVersion += 1;
      }
      body = {
        available,
        enabled: currentEnabled,
        version: currentVersion,
        policy_semantics: 'generation-enabled/v1',
        fallback_model: currentFallback,
        status: available ? 'ready' : 'The durable generation worker is unavailable.',
      };
    } else if (path === '/v1/models') {
      body = {
        default_model: 'primary',
        data: [
          { id: 'primary', label: 'Primary', available: true },
          { id: 'fallback', label: 'Fallback', available: true },
        ],
      };
    } else if (path === '/v1/backends') {
      body = { connected: true };
    } else if (path === '/v1/system') {
      body = { maintenance: { active: false }, deployment: { version: 'test', build_sha: 'test' } };
    } else if (path === '/sessions/') {
      body = { sessions: [] };
    } else if (path.includes('/compile')) {
      body = { node_count: 0, word_count: 0, missing_artifact_ids: [] };
    } else if (path === '/v1/manuscript') {
      body = { nodes: [] };
    } else if (path.includes('/v1/story/characters')) {
      body = { characters: [] };
    } else if (path.includes('/v1/story/world-rules')) {
      body = { rules: [] };
    } else if (path.includes('/v1/story/forbidden')) {
      body = { forbidden: [] };
    } else if (path === '/v1/entities') {
      body = { entities: [] };
    } else if (path.includes('/v1/story/captures')) {
      body = { captures: [] };
    } else if (path === '/v1/artifacts') {
      body = { artifacts: [] };
    } else if (path === '/v1/project/snapshots') {
      body = { snapshots: [] };
    } else if (path === '/v1/governed-chat/pending') {
      body = { pending: null };
    } else if (path === '/v1/markdown') {
      const posted = JSON.parse(request.postData() || '{}');
      body = { html: posted.content || '' };
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  return savedSettings;
}

test('generation control is prominent and unavailable state is explicit', async ({ page }) => {
  await mockWritingRoom(page, false);
  await page.goto('/');
  await expect(page.locator('#generation-switch')).toHaveText('Generation unavailable');
  await page.locator('#generation-switch').click();

  await expect(page.getByRole('heading', { name: 'Generation', exact: true })).toBeVisible();
  await expect(page.locator('#generation-enabled')).toBeDisabled();
  await expect(page.locator('#generation-reliability-status')).toContainText('unavailable');
  await expect(page.locator('#generation-unavailable-control')).toBeVisible();
  await expect(page.locator('#prompt')).toBeDisabled();
  await expect(page.locator('#send')).toBeDisabled();
  await expect(page.getByText('What does this change?')).toBeVisible();
});

test('paused composer exposes authorized enablement before submission', async ({ page }) => {
  const saved = await mockWritingRoom(page, true);
  await page.goto('/');
  await expect(page.locator('#generation-switch')).toHaveText('Generation paused');
  await expect(page.locator('#prompt')).toBeDisabled();
  await expect(page.locator('#send')).toBeDisabled();
  const paused = page.locator('#generation-paused-control');
  await expect(paused).toBeVisible();
  await expect(paused).toHaveText('Generation is paused for this project. Enable generation');
  await paused.click();

  await expect(page.locator('#generation-enabled')).toBeFocused();
  await page.locator('#generation-enabled').check();
  await expect(page.locator('#fallback-model')).toBeEnabled();
  await page.locator('#fallback-model').selectOption('fallback');
  await page.locator('#save-project').click();

  await expect.poll(() => saved.length).toBe(1);
  expect(saved[0]).toMatchObject({
    enabled: true, expected_version: 0, fallback_model: 'fallback',
  });
  await expect(page.locator('#generation-switch')).toHaveText('Generation enabled');
  await expect(page.locator('#generation-paused-control')).toBeHidden();
  await expect(page.locator('#prompt')).toBeEnabled();
  await expect(page.locator('#send')).toBeEnabled();
});

async function mockExistingSession(page: Page, messages: Array<Record<string, unknown>> = []) {
  const session = {
    id: 'session-1', title: 'Interrupted scene', model: 'primary', messages,
    message_count: messages.length, revision: messages.length ? 1 : 0,
  };
  await page.route('**/sessions/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = path === '/sessions/'
      ? { sessions: [{ ...session, messages: undefined }] }
      : session;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  return session;
}

test('reload reconciles a lost acknowledgement and separates configured from observed identity', async ({ page }) => {
  await mockWritingRoom(page, true, true);
  const messages = [
    { id: 'user-1', role: 'user', content: 'Continue once.', timestamp: '2026-09-07T00:00:00Z' },
    {
      id: 'assistant-1', role: 'assistant', content: 'Exactly one continuation.',
      timestamp: '2026-09-07T00:00:01Z', provider_id: 'openrouter', model_id: 'configured/route',
      accounting: {
        configured_provider_id: 'openrouter', configured_model_id: 'configured/route',
        observed_provider_id: 'openrouter', observed_model_id: 'actual/model',
        observed_identity_status: 'attested',
        reported_total_tokens: 321, cost_status: 'estimated', cost_usd: 0.0042,
        cost_note: 'Estimated from configured token rates.',
      },
    },
  ];
  await mockExistingSession(page, messages);
  await page.route('**/v1/generations**', async (route) => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ generations: [{
        outcome: 'authored', request_id: 'generation-1', client_request_id: 'delivery-1',
        committed_messages: messages,
      }] }),
    });
  });
  await page.addInitScript(() => {
    localStorage.setItem('marginalia.project', 'default');
    localStorage.setItem(
      'marginalia.durable-generation.default.session-1',
      JSON.stringify({ id: 'delivery-1', content: 'Continue once.' }),
    );
    localStorage.setItem('marginalia:draft:default:session-1', 'Continue once.');
  });

  await page.goto('/');
  await page.locator('button.session', { hasText: 'Interrupted scene' }).click();
  await expect(page.getByText('Exactly one continuation.')).toBeVisible();
  await expect(page.getByText('Marginalia · configured openrouter / configured/route')).toBeVisible();
  await expect(page.locator('.message-accounting')).toHaveText(
    'Observed openrouter / actual/model · 321 tokens reported · Provider cost $0.0042 (estimated)',
  );
  await expect(page.locator('#prompt')).toHaveValue('');
  await expect.poll(() => page.evaluate(() => localStorage.getItem(
    'marginalia.durable-generation.default.session-1',
  ))).toBeNull();
});

test('command provenance does not promote a configured label into observed identity', async ({ page }) => {
  await mockWritingRoom(page, true, true);
  await mockExistingSession(page, [{
    id: 'assistant-command', role: 'assistant', content: 'Command-backed continuation.',
    timestamp: '2026-09-07T00:00:01Z', provider_id: 'kimi-code-local', model_id: 'kimi-code/k3-256k',
    accounting: {
      configured_provider_id: 'kimi-code-local', configured_model_id: 'kimi-code/k3-256k',
      observed_provider_id: null, observed_model_id: null,
      observed_identity_status: 'unavailable', reported_total_tokens: null,
      cost_status: 'unavailable', cost_usd: null,
    },
  }]);

  await page.goto('/');
  await page.locator('button.session', { hasText: 'Interrupted scene' }).click();
  await expect(page.getByText(
    'Marginalia · configured kimi-code-local / kimi-code/k3-256k',
  )).toBeVisible();
  await expect(page.locator('.message-accounting')).toHaveText(
    'Observed provider/model unavailable · Token usage unavailable · Provider cost unavailable',
  );
});

test('rapid double submit creates one browser delivery', async ({ page }) => {
  await mockWritingRoom(page, true, true);
  await mockExistingSession(page);
  let posts = 0;
  await page.route('**/v1/chat/completions', async (route) => {
    posts += 1;
    await new Promise((resolve) => setTimeout(resolve, 100));
    await page.evaluate(() => {
      localStorage.setItem(
        'marginalia.durable-generation.default.session-1',
        JSON.stringify({ id: 'other-tab-delivery', content: 'Other tab prompt.' }),
      );
      localStorage.setItem('marginalia:draft:default:session-1', 'Other tab draft.');
    });
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        outcome: 'authored',
        committed_messages: [
          { id: 'u', role: 'user', content: 'One click too many.' },
          { id: 'a', role: 'assistant', content: 'One accepted result.' },
        ],
      }),
    });
  });

  await page.goto('/');
  await page.locator('button.session', { hasText: 'Interrupted scene' }).click();
  await page.locator('#prompt').fill('One click too many.');
  await page.evaluate(() => {
    const form = document.querySelector<HTMLFormElement>('#composer');
    form?.requestSubmit();
    form?.requestSubmit();
  });
  await expect(page.getByText('One accepted result.')).toBeVisible();
  expect(posts).toBe(1);
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem(
    'marginalia.durable-generation.default.session-1',
  ) || 'null')?.id)).toBe('other-tab-delivery');
  await expect.poll(() => page.evaluate(() => localStorage.getItem(
    'marginalia:draft:default:session-1',
  ))).toBe('Other tab draft.');
});
