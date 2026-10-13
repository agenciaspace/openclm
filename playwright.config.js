const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './e2e',
  workers: 1,
  retries: 0,
  reporter: [['list'], ['json', { outputFile: 'artifacts/browser-results.json' }]],
  use: { baseURL: 'http://127.0.0.1:38787/openclm/', trace: 'retain-on-failure' },
  projects: [
    { name: 'chromium-desktop', use: { browserName: 'chromium', viewport: { width: 1440, height: 900 } } },
    { name: 'chromium-phone', use: { browserName: 'chromium', viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
    { name: 'webkit-tablet', use: { browserName: 'webkit', viewport: { width: 820, height: 1180 }, isMobile: true, hasTouch: true } },
    { name: 'firefox-desktop', use: { browserName: 'firefox', viewport: { width: 1280, height: 800 } } },
  ],
  webServer: {
    command: '.venv/bin/python -m scripts.serve_browser_audit',
    url: 'http://127.0.0.1:38787/openclm/',
    reuseExistingServer: false,
    timeout: 30000,
  },
});
