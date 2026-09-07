import { expect, Page, test } from '@playwright/test';

async function mockWritingRoom(page: Page, available: boolean) {
  const savedSettings: Array<Record<string, unknown>> = [];
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
      if (request.method() === 'PUT') savedSettings.push(JSON.parse(request.postData() || '{}'));
      body = {
        available,
        enabled: request.method() === 'PUT' ? savedSettings.at(-1)?.enabled : false,
        fallback_model: request.method() === 'PUT' ? savedSettings.at(-1)?.fallback_model : null,
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
    } else if (path.includes('/fiction/characters')) {
      body = { characters: [] };
    } else if (path.includes('/fiction/world-rules')) {
      body = { rules: [] };
    } else if (path.includes('/fiction/forbidden')) {
      body = { forbidden: [] };
    } else if (path === '/v1/entities') {
      body = { entities: [] };
    } else if (path.includes('/fiction/captures')) {
      body = { captures: [] };
    } else if (path === '/governor/artifacts') {
      body = { artifacts: [] };
    } else if (path === '/v1/project/snapshots') {
      body = { snapshots: [] };
    } else if (path === '/v1/governed-chat/pending') {
      body = { pending: null };
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  return savedSettings;
}

test('reliability control is prominent and unavailable state is explicit', async ({ page }) => {
  await mockWritingRoom(page, false);
  await page.goto('/');
  await page.locator('#edit-project').click();

  await expect(page.getByRole('heading', { name: 'Generation reliability' })).toBeVisible();
  await expect(page.locator('#durable-generation')).toBeDisabled();
  await expect(page.locator('#generation-reliability-status')).toContainText('unavailable');
});

test('writer can enable custody and choose confirmed-failure fallback', async ({ page }) => {
  const saved = await mockWritingRoom(page, true);
  await page.goto('/');
  await page.locator('#edit-project').click();

  await page.locator('#durable-generation').check();
  await expect(page.locator('#fallback-model')).toBeEnabled();
  await page.locator('#fallback-model').selectOption('fallback');
  await page.locator('#save-project').click();

  await expect.poll(() => saved.length).toBe(1);
  expect(saved[0]).toMatchObject({ enabled: true, fallback_model: 'fallback' });
});
