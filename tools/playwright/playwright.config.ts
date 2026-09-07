import { defineConfig } from '@playwright/test';
import path from 'node:path';

const projectRoot = path.resolve(__dirname, '../..');

export default defineConfig({
  testDir: '.',
  timeout: 15_000,
  fullyParallel: false,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:18765',
    browserName: 'chromium',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'python3 -m http.server 18765 --bind 127.0.0.1 --directory src/gov_webui/static',
    cwd: projectRoot,
    url: 'http://127.0.0.1:18765',
    reuseExistingServer: false,
  },
});
